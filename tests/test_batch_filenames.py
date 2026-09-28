"""Testes da sanitização estrutural do filename opcional de item de lote.

(Etapa 9.3).

Não testa persistência.
"""

import pytest

from services.batch_filenames import (
    InvalidFilenameError,
    sanitize_batch_filename,
    sanitize_batch_filenames,
)


def test_none_e_none():

    assert sanitize_batch_filename(None) is None


def test_string_vazia_ou_so_espacos_vira_none():

    assert sanitize_batch_filename("") is None
    assert sanitize_batch_filename("   ") is None


def test_nome_valido_e_normalizado_sem_espacos_nas_pontas():

    assert sanitize_batch_filename("  meu video.mp4  ") == "meu video.mp4"


def test_extensao_mp4_e_adicionada_quando_ausente():

    assert sanitize_batch_filename("meu-video") == "meu-video.mp4"


def test_extensao_mp4_existente_nao_e_duplicada_preservando_maiusculas():

    assert sanitize_batch_filename("MEU-VIDEO.MP4") == "MEU-VIDEO.MP4"


def test_nome_composto_so_de_pontos_e_rejeitado():

    with pytest.raises(InvalidFilenameError):
        sanitize_batch_filename(".")


@pytest.mark.parametrize(
    "nome",
    [
        "../arquivo",
        r"..\arquivo",
        r"C:\arquivo",
        "/arquivo",
        "pasta/../../etc/passwd",
        "..",
        "a/../b",
    ],
)
def test_rejeita_tentativas_de_path_traversal(nome):

    with pytest.raises(InvalidFilenameError):
        sanitize_batch_filename(nome)


@pytest.mark.parametrize(
    "nome",
    [
        r"a\<b>.mp4",
        "nome;rm -rf.mp4",
        "nome\x00.mp4",
        "a" * 201,
    ],
)
def test_rejeita_caracteres_fora_do_conjunto_seguro_ou_nome_muito_longo(nome):

    with pytest.raises(InvalidFilenameError):
        sanitize_batch_filename(nome)


def test_lista_sem_duplicatas_passa():

    assert sanitize_batch_filenames(
        ["a.mp4", "b.mp4", None]
    ) == ["a.mp4", "b.mp4", None]


def test_varios_none_nao_sao_duplicata_entre_si():

    assert sanitize_batch_filenames(
        [None, None, None]
    ) == [None, None, None]


def test_lista_com_duplicatas_e_permitida():

    assert sanitize_batch_filenames(
        ["a.mp4", "b.mp4", "a.mp4"]
    ) == ["a.mp4", "b.mp4", "a.mp4"]


def test_duplicata_e_permitida_mesmo_com_espacos_diferentes_nas_pontas():

    assert sanitize_batch_filenames(
        ["a.mp4", " a.mp4 "]
    ) == ["a.mp4", "a.mp4"]