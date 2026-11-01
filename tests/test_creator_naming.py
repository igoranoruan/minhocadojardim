"""ETAPA B8 (pedido do Igor, 01/11/2026): opção "Nomear com o @ do criador" -- desmarcada por
padrão. Cobre as 3 camadas:

- download/creator_handle.py: qual campo do `info` do yt-dlp vira o @ em cada plataforma
  (conferido no código-fonte do yt-dlp) e o que é recusado.
- services/creator_filename.py: formato "@x.mp4" / "@x 2.mp4" e o alocador do lote.
- services/generation_flow.py + rotas: prioridade (nome digitado > @ > padrão), numeração no lote,
  item que falha não gasta número, opção desmarcada = comportamento de sempre, e o nome chegando
  de fato ao download (individual e ZIP).
"""
import json
import zipfile
from io import BytesIO
from unittest.mock import patch
from urllib.parse import unquote

import pytest

from database.models import Generation
from download.creator_handle import extract_creator_handle
from download.errors import DownloadFailedError
from download.platform import Platform
from helpers_generation_flow import fake_download_result, fake_processing_result, login_directly
from helpers_usage import NOW, give
from services.creator_filename import CreatorFilenameAllocator, creator_filename
from services.generation_flow import generate_from_url, run_batch
from services.usage import reserve_batch
from utils.time_sp import resolve_now


@pytest.fixture(autouse=True)
def diretorio_de_storage(tmp_path, monkeypatch):
    monkeypatch.setattr("services.result_storage.RESULT_STORAGE_DIR", str(tmp_path / "results"))


# ============================================================================ download/creator_handle.py
@pytest.mark.parametrize(
    ("platform", "info", "esperado"),
    [
        # Instagram: username em `channel`; uploader_id é numérico, uploader é o nome de exibição.
        (Platform.INSTAGRAM, {"channel": "naomipq", "uploader_id": "2815873", "uploader": "B E A U T Y"}, "naomipq"),
        # TikTok: @ em `uploader`; `channel` é o apelido de exibição.
        (Platform.TIKTOK, {"uploader": "patrox", "channel": "patroX Oficial", "uploader_id": "18702747"}, "patrox"),
        # YouTube: "@handle" em `uploader_id` -- o "@" é removido.
        (Platform.YOUTUBE, {"uploader_id": "@BlenderOfficial", "uploader": "Blender"}, "BlenderOfficial"),
        # YouTube com id de canal antigo ("UC..."): não é um @, nada de crédito inventado.
        (Platform.YOUTUBE, {"uploader_id": "UCSMOQeBJ2RAnuFungnQOxLg"}, None),
        # Pinterest: o yt-dlp não expõe username -- sempre None.
        (Platform.PINTEREST, {"uploader": "CoolCrazyGadgets", "uploader_id": "1084664028912989237"}, None),
        # Campo ausente, vazio, numérico puro, com espaço/emoji/barra, ou só pontuação -> None.
        (Platform.INSTAGRAM, {}, None),
        (Platform.INSTAGRAM, {"channel": "   "}, None),
        (Platform.TIKTOK, {"uploader": "123456789"}, None),
        (Platform.TIKTOK, {"uploader": "nome com espaco"}, None),
        (Platform.TIKTOK, {"uploader": "emoji😀"}, None),
        (Platform.INSTAGRAM, {"channel": "../etc/passwd"}, None),
        (Platform.INSTAGRAM, {"channel": "..."}, None),
        (Platform.INSTAGRAM, {"channel": "a" * 51}, None),
        # Pontos e underscores (comuns em @ do Instagram) são aceitos.
        (Platform.INSTAGRAM, {"channel": "klango.mp4_oficial"}, "klango.mp4_oficial"),
    ],
)
def test_extract_creator_handle(platform, info, esperado):
    assert extract_creator_handle(platform, info) == esperado


def test_extract_creator_handle_ignora_info_que_nao_e_dict():
    assert extract_creator_handle(Platform.TIKTOK, None) is None
    assert extract_creator_handle(Platform.TIKTOK, ["patrox"]) is None


# ============================================================================ services/creator_filename.py
def test_formato_do_nome_com_numero_separado_por_espaco():
    """"@klango1" seria o @ de OUTRA conta -- o número vem separado por espaço (combinado com o Igor)."""
    assert creator_filename("klango") == "@klango.mp4"
    assert creator_filename("klango", 2) == "@klango 2.mp4"
    assert creator_filename("klango", 10) == "@klango 10.mp4"
    with pytest.raises(ValueError):
        creator_filename("klango", 0)


def test_alocador_numera_repeticoes_do_mesmo_criador_na_ordem():
    alocador = CreatorFilenameAllocator()
    assert [alocador.allocate(h) for h in ["klango", "outro", "klango", "klango"]] == [
        "@klango.mp4", "@outro.mp4", "@klango 2.mp4", "@klango 3.mp4",
    ]


def test_alocador_nunca_repete_nome_ja_usado_no_lote_nem_por_maiusculas():
    alocador = CreatorFilenameAllocator(["@Klango.mp4", None, "video.mp4", "@klango 2.mp4"])
    assert alocador.allocate("klango") == "@klango 3.mp4"


# ============================================================================ geração avulsa
def _arquivos(diretorio, n):
    pares = []
    for i in range(n):
        d = diretorio / f"baixado{i}.mp4"
        d.write_bytes(b"x")
        o = diretorio / f"saida{i}.mp4"
        o.write_bytes(b"y")
        pares.append((d, o))
    return pares


def _gerar_avulso(session, usuario, tmp_path, *, handle, filename=None, name_by_creator):
    (d, o), = _arquivos(tmp_path, 1)
    with patch("services.generation_flow.download_video", return_value=fake_download_result(d, creator_handle=handle)), \
         patch("services.generation_flow.process_video", return_value=fake_processing_result(o)):
        resultado = generate_from_url(
            session, user=usuario, url="https://www.tiktok.com/@patrox/video/1",
            filename=filename, name_by_creator=name_by_creator,
        )
    session.expire_all()
    return resultado, session.get(Generation, resultado.generation_id)


def test_avulso_com_opcao_marcada_vira_arroba_do_criador(factory, session, tmp_path):
    resultado, linha = _gerar_avulso(session, factory.user(), tmp_path, handle="patrox", name_by_creator=True)
    assert resultado.display_filename == "@patrox.mp4"
    assert linha.display_filename == "@patrox.mp4"


def test_avulso_com_opcao_desmarcada_continua_como_sempre(factory, session, tmp_path):
    """Padrão do site (desmarcada): mesmo com o @ disponível, nada muda -- klango-xxxx de sempre."""
    resultado, linha = _gerar_avulso(session, factory.user(), tmp_path, handle="patrox", name_by_creator=False)
    assert resultado.display_filename is None
    assert linha.display_filename is None


def test_avulso_nome_digitado_vence_o_arroba(factory, session, tmp_path):
    resultado, linha = _gerar_avulso(
        session, factory.user(), tmp_path, handle="patrox", filename="meu video", name_by_creator=True,
    )
    assert resultado.display_filename is None  # nada decidido depois do download
    assert linha.display_filename == "meu video.mp4"  # o nome digitado continua intacto


def test_avulso_sem_arroba_da_plataforma_cai_no_padrao(factory, session, tmp_path):
    resultado, linha = _gerar_avulso(session, factory.user(), tmp_path, handle=None, name_by_creator=True)
    assert resultado.display_filename is None
    assert linha.display_filename is None


# ============================================================================ lote (run_batch)
def _lote(factory, session, usuario, n, display_filenames=None):
    give(factory, session, usuario, "weekly", now=NOW)
    return reserve_batch(
        session, user_id=usuario.id, request_id="lote-criador", size=n, now=NOW,
        display_filenames=display_filenames,
    )


def _nomes_no_banco(session, generation_ids):
    session.expire_all()
    return [session.get(Generation, gid).display_filename for gid in generation_ids]


def test_lote_numera_o_mesmo_criador_e_respeita_a_ordem(factory, session, tmp_path):
    usuario = factory.user()
    reserva = _lote(factory, session, usuario, 4)
    pares = _arquivos(tmp_path, 4)
    handles = ["klango", "outro", "klango", None]  # o último: plataforma sem @ (ex.: Pinterest)
    items = list(zip(reserva.generation_ids, [f"https://www.tiktok.com/@x/video/{i}" for i in range(4)]))

    with patch("services.generation_flow.download_video", side_effect=[
        fake_download_result(d, creator_handle=h) for (d, _), h in zip(pares, handles)
    ]), patch("services.generation_flow.process_video", side_effect=[fake_processing_result(o) for _, o in pares]):
        resultados = run_batch(session, items=items, name_by_creator=True)

    assert [r.outcome.display_filename for r in resultados] == ["@klango.mp4", "@outro.mp4", "@klango 2.mp4", None]
    assert _nomes_no_banco(session, reserva.generation_ids) == ["@klango.mp4", "@outro.mp4", "@klango 2.mp4", None]


def test_lote_nome_digitado_vence_e_nunca_e_repetido_pelo_automatico(factory, session, tmp_path):
    usuario = factory.user()
    # Item 1 digitado, itens 2 e 3 sem nome -- todos do mesmo criador.
    reserva = _lote(factory, session, usuario, 3, display_filenames=["@klango.mp4", None, None])
    pares = _arquivos(tmp_path, 3)
    items = list(zip(reserva.generation_ids, [f"https://www.tiktok.com/@x/video/{i}" for i in range(3)]))

    with patch("services.generation_flow.download_video", side_effect=[
        fake_download_result(d, creator_handle="klango") for d, _ in pares
    ]), patch("services.generation_flow.process_video", side_effect=[fake_processing_result(o) for _, o in pares]):
        run_batch(session, items=items, name_by_creator=True)

    assert _nomes_no_banco(session, reserva.generation_ids) == ["@klango.mp4", "@klango 2.mp4", "@klango 3.mp4"]


def test_lote_item_que_falha_nao_gasta_numero(factory, session, tmp_path):
    usuario = factory.user()
    reserva = _lote(factory, session, usuario, 2)
    pares = _arquivos(tmp_path, 2)
    items = list(zip(reserva.generation_ids, ["https://www.tiktok.com/@x/video/1", "https://www.tiktok.com/@x/video/2"]))

    with patch("services.generation_flow.download_video", side_effect=[
        DownloadFailedError("falhou"),
        fake_download_result(pares[1][0], creator_handle="klango"),
    ]), patch("services.generation_flow.process_video", return_value=fake_processing_result(pares[1][1])):
        resultados = run_batch(session, items=items, name_by_creator=True)

    assert [r.ok for r in resultados] == [False, True]
    assert resultados[1].outcome.display_filename == "@klango.mp4"  # não "@klango 2.mp4"


def test_lote_com_opcao_desmarcada_continua_como_sempre(factory, session, tmp_path):
    usuario = factory.user()
    reserva = _lote(factory, session, usuario, 2)
    pares = _arquivos(tmp_path, 2)
    items = list(zip(reserva.generation_ids, ["https://www.tiktok.com/@x/video/1", "https://www.tiktok.com/@x/video/2"]))

    with patch("services.generation_flow.download_video", side_effect=[
        fake_download_result(d, creator_handle="klango") for d, _ in pares
    ]), patch("services.generation_flow.process_video", side_effect=[fake_processing_result(o) for _, o in pares]):
        run_batch(session, items=items)  # default: name_by_creator=False

    assert _nomes_no_banco(session, reserva.generation_ids) == [None, None]


# ============================================================================ rotas (ponta a ponta)
def _eventos(resposta):
    eventos = []
    for bloco in resposta.text.strip("\n").split("\n\n"):
        if not bloco.strip():
            continue
        linhas = bloco.split("\n")
        eventos.append((linhas[0].removeprefix("event: "), json.loads(linhas[1].removeprefix("data: "))))
    return eventos


def test_rota_de_lote_nomeia_pelo_criador_no_evento_e_no_zip(auth_client, factory, session, tmp_path):
    usuario = factory.user()
    login_directly(auth_client, session, usuario)
    give(factory, session, usuario, "weekly", now=resolve_now())
    pares = _arquivos(tmp_path, 3)
    urls = [f"https://www.tiktok.com/@klango/video/{i}" for i in range(3)]

    with patch("services.generation_flow.download_video", side_effect=[
        fake_download_result(d, creator_handle="klango") for d, _ in pares
    ]), patch("services.generation_flow.process_video", side_effect=[fake_processing_result(o) for _, o in pares]):
        resposta = auth_client.post("/api/download-batch", json={
            "request_id": "criador-1",
            "name_by_creator": True,
            "items": [{"url": urls[0]}, {"url": urls[1], "filename": "meu nome"}, {"url": urls[2]}],
        })

    eventos = _eventos(resposta)
    assert eventos[-1][0] == "complete"
    assert [d["filename"] for t, d in eventos if t == "item_complete"] == ["@klango.mp4", "meu nome.mp4", "@klango 2.mp4"]

    batch_id = eventos[0][1]["batch_id"]
    zip_resposta = auth_client.get(f"/api/batches/{batch_id}/download")
    assert zip_resposta.status_code == 200
    with zipfile.ZipFile(BytesIO(zip_resposta.content)) as z:
        assert sorted(z.namelist()) == sorted(["@klango.mp4", "meu nome.mp4", "@klango 2.mp4"])


def test_rota_de_lote_sem_o_campo_continua_como_sempre(auth_client, factory, session, tmp_path):
    """Cliente antigo (sem `name_by_creator` no corpo): default False, nada muda."""
    usuario = factory.user()
    login_directly(auth_client, session, usuario)
    give(factory, session, usuario, "weekly", now=resolve_now())
    (d, o), = _arquivos(tmp_path, 1)

    with patch("services.generation_flow.download_video", return_value=fake_download_result(d, creator_handle="klango")), \
         patch("services.generation_flow.process_video", return_value=fake_processing_result(o)):
        resposta = auth_client.post("/api/download-batch", json={
            "request_id": "criador-2", "items": [{"url": "https://www.tiktok.com/@klango/video/1"}],
        })

    item = next(d for t, d in _eventos(resposta) if t == "item_complete")
    assert item["filename"] is None


def test_rota_avulsa_nomeia_pelo_criador_no_download(auth_client, factory, session, tmp_path):
    usuario = factory.user()
    login_directly(auth_client, session, usuario)
    give(factory, session, usuario, "weekly", now=resolve_now())
    (d, o), = _arquivos(tmp_path, 1)

    with patch("services.generation_flow.download_video", return_value=fake_download_result(d, creator_handle="patrox")), \
         patch("services.generation_flow.process_video", return_value=fake_processing_result(o)):
        resposta = auth_client.post("/api/generations", json={
            "url": "https://www.tiktok.com/@patrox/video/1", "name_by_creator": True,
        })

    completo = next(d for t, d in _eventos(resposta) if t == "complete")
    assert completo["filename"] == "@patrox.mp4"

    download = auth_client.get(f"/api/generations/{completo['generation_id']}/download")
    assert download.status_code == 200
    assert "@patrox.mp4" in unquote(download.headers["content-disposition"])
