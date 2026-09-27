"""Monta e executa o comando FFmpeg da V1: MP4 H.264 (libx264, preset veryfast, CRF 23) + AAC
(quando há áudio), metadados e capítulos removidos, faststart, sem legendas/streams de dados na
saída.

Segurança: nunca ativar o parâmetro de shell do subprocess, nunca usar os.system, nunca montar o
comando como string — sempre uma lista fixa de argumentos passada ao subprocess. Os dois únicos
valores externos que entram na lista são os CAMINHOS de entrada/saída (gerados por
processor/tempfiles.py, nunca por dado do usuário) — nenhum dado de fora vira FLAG do FFmpeg.

Timeout: continua 180s (PROCESSING_TIMEOUT_SECONDS), sem nenhuma alteração de valor. Garantia de
que nenhum processo fica órfão: no timeout, o processo é morto e as duas threads de leitura
(stdout/stderr) são aguardadas por um prazo curto antes desta função devolver o controle.

Progresso real (barra de progresso, etapa nova): `-progress pipe:1 -nostats` faz o FFmpeg emitir,
periodicamente, um bloco de linhas `chave=valor` em stdout, terminado por `progress=continue` (ou
`progress=end` no último). Nada disso conflita com `-loglevel error` (que continua controlando só
o log de erro, em stderr) nem com a classificação de falha existente.

Por que duas threads, e não `select`: `select.select()` no Windows só aceita sockets, não os
HANDLEs de pipe anônimo que o subprocess cria — inviável nesta plataforma sem uma dependência nova
(pywin32) ou reescrever tudo em asyncio (o que exigiria tornar toda a cadeia síncrona, incluindo a
sessão do banco, assíncrona — fora do escopo desta etapa). Duas threads (uma por pipe, cada uma
fazendo `for linha in stream:` bloqueante, publicando numa mesma queue.Queue) é exatamente o
padrão que a própria stdlib do CPython já usa dentro de `subprocess.communicate()` no Windows —
portável, sem dependência nova.
"""
import logging
import queue
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path

from config import PROCESSING_TIMEOUT_SECONDS, get_settings
from processor.errors import FfmpegFailedError, FfmpegUnavailableError, ProcessingTimeoutError, ProcessorError
from processor.probe import resolve_executable

logger = logging.getLogger("minhoca")

# Limite do que guardamos do stderr do FFmpeg (log-only; nunca vai para o usuário). Processos que
# travam produzindo log não devem crescer sem controle em memória.
_STDERR_CAPTURE_LIMIT_BYTES = 8 * 1024
# Intervalo entre tentativas de ler o processo depois de kill(), antes do wait() final bloqueante.
_KILL_WAIT_SECONDS = 5
# Intervalo máximo que a thread principal fica bloqueada em queue.get() antes de reconferir o
# prazo do timeout — não afeta o timeout em si, só a granularidade da checagem.
_POLL_INTERVAL_SECONDS = 0.5


def build_args(*, executable: str, input_path: Path, output_path: Path, has_audio: bool) -> list[str]:
    """Lista de argumentos do FFmpeg. `has_audio` decide TODO o tratamento de áudio: se False,
    nenhum `-map` nem `-c:a` de áudio entram na lista (saída só com vídeo; nada de áudio artificial).
    `-progress pipe:1 -nostats`: progresso real em stdout, sem poluir o stderr com as estatísticas
    periódicas que o FFmpeg imprimiria por padrão."""
    args = [
        executable,
        "-hide_banner",
        "-loglevel", "error",
        "-nostats",
        "-y",  # sobrescreve o arquivo de saída (nome gerado por nós, nunca existe antes)
        "-i", str(input_path),
        "-map", "0:v:0",
    ]
    if has_audio:
        args += ["-map", "0:a:0"]
    args += [
        "-map_metadata", "-1",
        "-map_chapters", "-1",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "23",
    ]
    if has_audio:
        args += ["-c:a", "aac"]
    args += [
        "-movflags", "+faststart",
        "-progress", "pipe:1",
        "-f", "mp4",
        str(output_path),
    ]
    return args


_UNKNOWN_ENCODER_MARKERS = ("unknown encoder", "encoder not found")


def _classify_failure(stderr_text: str, returncode: int) -> ProcessorError:
    lowered = stderr_text.lower()
    if any(marker in lowered for marker in _UNKNOWN_ENCODER_MARKERS) and "libx264" in lowered:
        return FfmpegUnavailableError(f"encoder libx264 indisponível no FFmpeg (código {returncode})")
    return FfmpegFailedError(f"FFmpeg terminou com código {returncode}: {stderr_text[:300]}")


def _seconds_from_tick(tick: dict[str, str]) -> float | None:
    """Tempo já processado (segundos), extraído de um "tick" (um bloco de linhas chave=valor) do
    -progress do FFmpeg, na ordem de preferência pedida: out_time_us (microssegundos, inequívoco)
    -> out_time (timestamp HH:MM:SS.ffffff, também inequívoco) -> out_time_ms (fallback -- o nome
    sugere milissegundos, mas é o campo historicamente menos confiável entre versões do FFmpeg,
    por isso só usado se os outros dois não estiverem presentes)."""
    if "out_time_us" in tick:
        try:
            return int(tick["out_time_us"]) / 1_000_000
        except (TypeError, ValueError):
            pass
    if "out_time" in tick:
        try:
            h, m, s = tick["out_time"].split(":")
            return int(h) * 3600 + int(m) * 60 + float(s)
        except (TypeError, ValueError):
            pass
    if "out_time_ms" in tick:
        try:
            return int(tick["out_time_ms"]) / 1000
        except (TypeError, ValueError):
            pass
    return None


def _percent_from_tick(tick: dict[str, str], duration_seconds: float | None) -> float | None:
    """Percentual real (0-100) de um tick, ou None quando não dá para calcular um valor real --
    NUNCA um percentual inventado. duration_seconds None (vídeo sem duração conhecida) -> None,
    sempre. `progress=end` (o FFmpeg terminou) força 100 exatamente, sem depender do arredondamento
    do último tick."""
    if duration_seconds is None or duration_seconds <= 0:
        return None
    if tick.get("progress") == "end":
        return 100.0
    segundos = _seconds_from_tick(tick)
    if segundos is None:
        return None
    return max(0.0, min(100.0, segundos / duration_seconds * 100))


def _read_stdout_progress(
    stream, result_queue: queue.Queue, duration_seconds: float | None,
    on_progress: Callable[[float | None], None] | None,
) -> None:
    """Lê stdout linha a linha (bloqueante, numa thread própria) e monta cada "tick" do
    -progress; ao ver a linha "progress=...", o tick está completo -- calcula o percentual e
    chama on_progress (se houver). Uma falha do PRÓPRIO callback do chamador nunca pode derrubar
    a leitura (senão o pipe para de ser drenado e o processo trava)."""
    tick: dict[str, str] = {}
    try:
        for raw_line in stream:
            line = raw_line.strip()
            if not line or "=" not in line:
                continue
            key, _, value = line.partition("=")
            tick[key] = value
            if key == "progress":
                if on_progress is not None:
                    try:
                        on_progress(_percent_from_tick(tick, duration_seconds))
                    except Exception:
                        logger.warning("[PROCESSOR] callback de progresso falhou", exc_info=True)
                tick = {}
    finally:
        result_queue.put(("stdout_eof", None))


def _read_stderr(stream, result_queue: queue.Queue) -> None:
    """Lê stderr linha a linha (bloqueante, numa thread própria) até EOF, guardando só até o
    limite já existente (_STDERR_CAPTURE_LIMIT_BYTES) -- continua DRENANDO o pipe inteiro mesmo
    depois do limite, para nunca travar o processo por buffer cheio."""
    partes: list[str] = []
    tamanho = 0
    try:
        for raw_line in stream:
            if tamanho < _STDERR_CAPTURE_LIMIT_BYTES:
                partes.append(raw_line)
                tamanho += len(raw_line.encode("utf-8", errors="replace"))
    finally:
        result_queue.put(("stderr_done", "".join(partes)))


def run_ffmpeg(
    *,
    input_path: Path,
    output_path: Path,
    has_audio: bool,
    duration_seconds: float | None = None,
    on_progress: Callable[[float | None], None] | None = None,
) -> None:
    """Executa o FFmpeg com timeout real (inalterado: PROCESSING_TIMEOUT_SECONDS). Levanta
    FfmpegUnavailableError, FfmpegFailedError ou ProcessingTimeoutError -- MESMAS exceções de
    antes, mesma classificação de erro. NUNCA deixa o processo órfão: no timeout, mata e aguarda
    (processo e as duas threads de leitura) antes de devolver o controle ao chamador.

    `duration_seconds` (a duração REAL do vídeo de entrada, já calculada por processor/probe.py
    antes desta função ser chamada) e `on_progress` são OPCIONAIS -- omitidos, o comportamento é
    idêntico ao de antes desta etapa, só que agora sempre com -progress/-nostats no comando (o
    FFmpeg emite os ticks de qualquer forma; se não há on_progress, simplesmente ninguém os lê
    além da thread que os descarta ao montar cada tick)."""
    settings = get_settings()
    executable = resolve_executable(settings.ffmpeg_path, what="FFmpeg", error_cls=FfmpegUnavailableError)
    args = build_args(executable=executable, input_path=input_path, output_path=output_path, has_audio=has_audio)

    try:
        process = subprocess.Popen(  # noqa: S603 - lista fixa de argumentos, nunca shell
            args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
    except FileNotFoundError:
        raise FfmpegUnavailableError(f"FFmpeg não pôde ser executado: {executable!r}") from None

    result_queue: queue.Queue = queue.Queue()
    thread_stdout = threading.Thread(
        target=_read_stdout_progress, args=(process.stdout, result_queue, duration_seconds, on_progress), daemon=True,
    )
    thread_stderr = threading.Thread(target=_read_stderr, args=(process.stderr, result_queue), daemon=True)
    thread_stdout.start()
    thread_stderr.start()

    prazo = time.monotonic() + PROCESSING_TIMEOUT_SECONDS
    stdout_concluido = False
    stderr_texto: str | None = None
    estourou = False
    while not (stdout_concluido and stderr_texto is not None):
        restante = prazo - time.monotonic()
        if restante <= 0:
            estourou = True
            break
        try:
            tipo, valor = result_queue.get(timeout=min(restante, _POLL_INTERVAL_SECONDS))
        except queue.Empty:
            continue
        if tipo == "stdout_eof":
            stdout_concluido = True
        elif tipo == "stderr_done":
            stderr_texto = valor

    if estourou:
        _kill_and_wait(process)
        thread_stdout.join(timeout=_KILL_WAIT_SECONDS)
        thread_stderr.join(timeout=_KILL_WAIT_SECONDS)
        logger.warning("[PROCESSOR] FFmpeg excedeu o timeout de %ss e foi encerrado", PROCESSING_TIMEOUT_SECONDS)
        raise ProcessingTimeoutError(f"FFmpeg excedeu {PROCESSING_TIMEOUT_SECONDS}s") from None

    process.wait()  # as duas threads já deram EOF -- o processo já terminou; só coleta o returncode

    if process.returncode != 0:
        stderr_text = (stderr_texto or "")[:_STDERR_CAPTURE_LIMIT_BYTES]
        logger.warning("[PROCESSOR] FFmpeg falhou (código %s): %s", process.returncode, stderr_text)
        raise _classify_failure(stderr_text, process.returncode)


def _kill_and_wait(process: subprocess.Popen) -> None:
    """Garante que o processo morre e é colhido (evita zumbi), mesmo se kill() já não puder mais
    afetá-lo (processo que terminou entre o timeout e esta chamada).

    Diferença desta etapa: NÃO chama mais process.communicate() -- as threads de leitura
    (stdout/stderr) já são as donas dos pipes agora; chamar communicate() aqui competiria com
    elas pela leitura dos mesmos pipes. kill() já faz os pipes fecharem do lado do processo, e as
    duas threads terminam sozinhas (run_ffmpeg dá join() nelas logo em seguida)."""
    process.kill()
    try:
        process.wait(timeout=_KILL_WAIT_SECONDS)
    except subprocess.TimeoutExpired:
        logger.error("[PROCESSOR] FFmpeg não respondeu a kill() em %ss", _KILL_WAIT_SECONDS)
