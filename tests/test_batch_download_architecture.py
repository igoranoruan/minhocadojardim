"""Arquitetura da correção cirúrgica pós-Etapa 9.3: GET /api/batches/{batch_id}/download não pode
decidir sozinha quais itens de um lote são baixáveis -- essa decisão (ownership, status,
storage_key, expiração, existência física) mora inteiramente em
services.usage.get_downloadable_batch_generations, a MESMA filosofia que
get_downloadable_generation já aplica para a geração avulsa (ver
tests/test_generation_flow_architecture.py::test_busca_da_geracao_para_download_nao_e_duplicada_na_rota
-- este arquivo é o equivalente para o download de LOTE).
"""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ROUTE_FILE = ROOT / "routes" / "generations.py"
USAGE_FILE = ROOT / "services" / "usage.py"


def _function_node(path: Path, name: str) -> ast.FunctionDef:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)


def _function_source(path: Path, name: str) -> str:
    return ast.unparse(_function_node(path, name))


def test_download_batch_nao_decide_elegibilidade_sozinha():
    """A rota não pode reimplementar nenhuma das checagens que já pertencem ao serviço: status,
    expiração, ou existência física -- só monta o ZIP a partir do que o serviço já filtrou."""
    fonte = _function_source(ROUTE_FILE, "download_batch")
    assert "get_downloadable_batch_generations(" in fonte
    for proibido in (
        "Generation.status", ".status ==", ".status !=",
        "output_expires_at", "resolve_now(", "result_storage.exists(", "select(Generation)",
    ):
        assert proibido not in fonte, proibido


def test_download_batch_continua_fina():
    """Teste desatualizado (auditoria de 04/10/2026): o limiar de 7 foi fixado no commit 698f5ed
    (quando a função tinha exatamente 7 statements). O commit 688c305 ("Nome padrão de download:
    klango-{aleatório} em vez de minhoca-{id sequencial}", correção de privacidade aprovada do
    CÉREBRO -- evita expor o batch_id sequencial no nome do arquivo) acrescentou UM statement novo
    (`token = uuid.uuid4().hex[:8]`), levando o total a 8 -- o limiar nunca foi atualizado para
    refletir essa mudança legítima. A rota continua fina; só o número de linhas mudou."""
    funcao = _function_node(ROUTE_FILE, "download_batch")
    assert len(funcao.body) <= 8, "routes/generations.py:download_batch deixou de ser fina"


def test_rota_nao_importa_mais_o_que_so_servia_para_a_decisao_de_elegibilidade():
    """select/resolve_now só eram usados para a checagem que agora é do serviço -- confirma que a
    rota não os importa mais (evita reintroduzir a regra de negócio por acidente)."""
    fonte = ROUTE_FILE.read_text(encoding="utf-8")
    assert "from sqlalchemy import select" not in fonte
    assert "from utils.time_sp import resolve_now" not in fonte
    assert "get_owned_batch" not in fonte  # encapsulado dentro do novo service, não mais chamado da rota


def test_get_downloadable_batch_generations_reaproveita_get_owned_batch():
    """Não duplica a checagem de ownership -- delega para get_owned_batch, a mesma usada antes."""
    fonte = _function_source(USAGE_FILE, "get_downloadable_batch_generations")
    assert "get_owned_batch(" in fonte


def _compara_status_com_completed(funcao: ast.FunctionDef) -> bool:
    """Procura, estruturalmente (via AST, nunca por correspondência textual de aspas), uma
    comparação de igualdade entre um atributo `.status` e o literal "completed" em qualquer lugar
    do corpo da função. `ast.unparse` pode normalizar strings para aspas simples ou duplas
    dependendo da versão do Python/da forma como o literal foi escrito no arquivo-fonte -- isso é
    só formatação, nunca deveria fazer um teste arquitetural falhar. Comparar o `ast.Constant.value`
    real (não o texto) é imune a essa variação."""
    for node in ast.walk(funcao):
        if isinstance(node, ast.Compare) and len(node.ops) == 1 and isinstance(node.ops[0], ast.Eq):
            operandos = (node.left, node.comparators[0])
            tem_status = any(isinstance(op, ast.Attribute) and op.attr == "status" for op in operandos)
            tem_completed = any(isinstance(op, ast.Constant) and op.value == "completed" for op in operandos)
            if tem_status and tem_completed:
                return True
    return False


def test_get_downloadable_batch_generations_usa_os_mesmos_criterios_de_elegibilidade():
    """Os MESMOS critérios que get_downloadable_generation já usa para a geração avulsa: status
    completed, storage_key presente, expiração e existência física -- centralizados aqui, não
    reimplementados do zero."""
    funcao = _function_node(USAGE_FILE, "get_downloadable_batch_generations")
    fonte = ast.unparse(funcao)
    assert "output_storage_key" in fonte
    assert "output_expires_at" in fonte
    assert "result_storage.exists(" in fonte
    assert _compara_status_com_completed(funcao), (
        "get_downloadable_batch_generations não compara .status == \"completed\" (verificado "
        "estruturalmente via AST, não por texto)"
    )


def test_usage_e_quem_importa_result_storage_para_a_checagem_de_existencia():
    fonte = USAGE_FILE.read_text(encoding="utf-8")
    assert "from services import result_storage" in fonte


def test_get_downloadable_batch_generations_levanta_batch_not_found_error_quando_vazio():
    fonte = _function_source(USAGE_FILE, "get_downloadable_batch_generations")
    assert "raise BatchNotFoundError()" in fonte
