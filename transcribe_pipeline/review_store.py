from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any
import re
import time

from .config import Paths
from .render import write_docx_if_available, write_markdown, write_nvivo_tsv, write_srt, write_turns_csv, write_turns_tsv, write_vtt
from .utils import now_utc, read_json, write_json


REVIEW_SCHEMA_VERSION = 1
TURN_ID_RE = re.compile(r"^turn_(\d+)$")


def review_path(paths: Paths, interview_id: str) -> Path:
    return paths.review_dir / "edits" / f"{interview_id}.review.json"


def canonical_path(paths: Paths, interview_id: str) -> Path:
    return paths.canonical_dir / "json" / f"{interview_id}.canonical.json"


def load_canonical_transcript(paths: Paths, interview_id: str) -> dict[str, Any]:
    path = canonical_path(paths, interview_id)
    if not path.exists():
        raise FileNotFoundError(f"Missing canonical transcript: {path}")
    return read_json(path)


def backup_review_file(paths: Paths, interview_id: str) -> Path | None:
    """Copia de seguranca da revisao antes de qualquer sobrescrita.

    So gera backup quando o arquivo existente carrega trabalho humano
    (edits nao-vazio ou algum turno editado) ou esta ilegivel (corrompido:
    preservar os bytes antes de substituir). Revisao pristina nao gera
    backup — refresh_unedited_reviews recria revisoes intactas de rotina e
    encheria a pasta.
    """
    path = review_path(paths, interview_id)
    if not path.exists():
        return None
    worth_keeping = True
    try:
        review = read_json(path)
        turns = (review.get("transcript") or {}).get("turns") or []
        worth_keeping = bool(review.get("edits")) or any(
            turn.get("edited") for turn in turns if isinstance(turn, dict)
        )
    except Exception:  # noqa: BLE001 - ilegivel: preservar os bytes mesmo assim
        worth_keeping = True
    if not worth_keeping:
        return None
    backup_dir = path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = backup_dir / f"{interview_id}.review.{stamp}.json"
    suffix = 2
    while target.exists():
        target = backup_dir / f"{interview_id}.review.{stamp}-{suffix}.json"
        suffix += 1
    target.write_bytes(path.read_bytes())
    return target


def create_review_from_canonical(paths: Paths, interview_id: str, reviewer: str = "") -> dict[str, Any]:
    backup_review_file(paths, interview_id)
    canonical = load_canonical_transcript(paths, interview_id)
    review = {
        "schema_version": REVIEW_SCHEMA_VERSION,
        "review_status": "draft",
        "reviewer": reviewer,
        "source": {
            "canonical_path": str(canonical_path(paths, interview_id)),
            "interview_id": interview_id,
        },
        "transcript": deepcopy(canonical),
        "edits": [],
    }
    normalize_review(review)
    save_review_transcript(paths, interview_id, review)
    return review


def load_review_transcript(paths: Paths, interview_id: str, create: bool = True) -> dict[str, Any]:
    path = review_path(paths, interview_id)
    if path.exists():
        review = read_json(path)
        before = deepcopy(review)
        normalized = normalize_review(review)
        if normalized != before:
            write_json(path, normalized)
        return normalized
    if create:
        return create_review_from_canonical(paths, interview_id)
    raise FileNotFoundError(f"Missing review transcript: {path}")


def save_review_transcript(paths: Paths, interview_id: str, review: dict[str, Any]) -> None:
    normalize_review(review)
    review["updated_at"] = now_utc()
    path = review_path(paths, interview_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, review)


def review_to_canonical(review: dict[str, Any]) -> dict[str, Any]:
    normalize_review(review)
    transcript = review.get("transcript")
    if not isinstance(transcript, dict):
        raise ValueError("Review file does not contain a transcript object.")
    return transcript


def export_review_outputs(paths: Paths, interview_id: str, formats: list[str] | None = None) -> list[Path]:
    formats = [item.lower() for item in (formats or ["md", "docx", "srt", "vtt", "csv", "tsv", "nvivo"])]
    review = load_review_transcript(paths, interview_id, create=False)
    canonical = review_to_canonical(review)
    output_dir = paths.review_dir / "final"
    exported: list[Path] = []

    if "md" in formats:
        path = output_dir / "md" / f"{interview_id}.reviewed.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        write_markdown(path, canonical)
        exported.append(path)
    if "docx" in formats:
        path = output_dir / "docx" / f"{interview_id}.reviewed.docx"
        path.parent.mkdir(parents=True, exist_ok=True)
        write_docx_if_available(path, canonical)
        if path.exists():
            exported.append(path)
    if "srt" in formats:
        path = output_dir / "srt" / f"{interview_id}.reviewed.srt"
        path.parent.mkdir(parents=True, exist_ok=True)
        write_srt(path, canonical)
        exported.append(path)
    if "vtt" in formats:
        path = output_dir / "vtt" / f"{interview_id}.reviewed.vtt"
        path.parent.mkdir(parents=True, exist_ok=True)
        write_vtt(path, canonical)
        exported.append(path)
    if "csv" in formats:
        path = output_dir / "csv" / f"{interview_id}.reviewed.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        write_turns_csv(path, canonical)
        exported.append(path)
    if "tsv" in formats:
        path = output_dir / "tsv" / f"{interview_id}.reviewed.tsv"
        path.parent.mkdir(parents=True, exist_ok=True)
        write_turns_tsv(path, canonical)
        exported.append(path)
    if "nvivo" in formats:
        path = output_dir / "nvivo" / f"{interview_id}.reviewed_nvivo.tsv"
        path.parent.mkdir(parents=True, exist_ok=True)
        write_nvivo_tsv(path, canonical)
        exported.append(path)
    return exported


def normalize_review(review: dict[str, Any]) -> dict[str, Any]:
    review.setdefault("schema_version", REVIEW_SCHEMA_VERSION)
    review.setdefault("review_status", "draft")
    review.setdefault("edits", [])
    transcript = review.get("transcript")
    if not isinstance(transcript, dict):
        return review
    turns = transcript.get("turns", [])
    if not isinstance(turns, list):
        transcript["turns"] = []
        return review
    seen: set[str] = set()
    next_id = 1
    for index, turn in enumerate(turns):
        if not isinstance(turn, dict):
            turns[index] = {}
            turn = turns[index]
        turn_id = str(turn.get("id") or "").strip()
        if not turn_id or turn_id in seen:
            while f"turn_{next_id:06d}" in seen:
                next_id += 1
            turn_id = f"turn_{next_id:06d}"
        turn["id"] = turn_id
        seen.add(turn_id)
        if "flags" not in turn or not isinstance(turn.get("flags"), list):
            turn["flags"] = []
        if "notes" not in turn:
            turn["notes"] = ""
        if "edited" not in turn:
            turn["edited"] = False
    return review


def find_turn_index(review: dict[str, Any], turn_id: str) -> int:
    turns = review_turns(review)
    for index, turn in enumerate(turns):
        if turn.get("id") == turn_id:
            return index
    raise KeyError(f"Turn not found: {turn_id}")


def review_turns(review: dict[str, Any]) -> list[dict[str, Any]]:
    normalize_review(review)
    transcript = review.get("transcript")
    if not isinstance(transcript, dict):
        raise ValueError("Review file does not contain a transcript object.")
    turns = transcript.get("turns")
    if not isinstance(turns, list):
        raise ValueError("Review transcript does not contain a turns list.")
    return turns


def set_turn_text(review: dict[str, Any], turn_id: str, text: str) -> None:
    turn = review_turns(review)[find_turn_index(review, turn_id)]
    turn["text"] = str(text).strip()
    turn["edited"] = True
    record_edit(review, "set_text", turn_id)


def set_turn_speaker_label(review: dict[str, Any], turn_id: str, human_label: str) -> None:
    turn = review_turns(review)[find_turn_index(review, turn_id)]
    turn["human_label"] = human_label
    turn["edited"] = True
    record_edit(review, "set_speaker", turn_id)


def apply_label_to_speaker_key(review: dict[str, Any], reference_key: str, human_label: str) -> int:
    """Aplica o rotulo a TODOS os turnos da voz identificada por reference_key.

    reference_key e o turn_speaker_key da voz (rotulo humano normalizado, ou o
    SPEAKER_XX tecnico quando ainda sem nome). Retorna quantos turnos mudaram.
    """
    label = " ".join(str(human_label).split())
    if not label:
        raise ValueError("Informe um nome de falante.")
    changed = 0
    for turn in review_turns(review):
        if turn_speaker_key(turn) != reference_key:
            continue
        if str(turn.get("human_label") or "").strip() == label:
            continue
        turn["human_label"] = label
        turn["edited"] = True
        changed += 1
    if changed:
        record_edit(review, "set_speaker_all", reference_key)
    return changed


def apply_label_to_raw_speaker(review: dict[str, Any], speaker_id: str, human_label: str, dominant_key: str) -> int:
    """Aplica o rotulo aos turnos da voz CRUA speaker_id (SPEAKER_NN).

    So relabela turnos cujo turn_speaker_key atual e o dominant_key da voz —
    um turno que o usuario ja reatribuiu a mao (key divergente) fica intocado.
    Imune a rotulos nao-injetivos (dois SPEAKER_NN com o mesmo rotulo default);
    duas vozes podem receber o MESMO nome (fusao legitima). Retorna quantos
    turnos mudaram.
    """
    label = " ".join(str(human_label).split())
    if not label:
        raise ValueError("Informe um nome de falante.")
    changed = 0
    for turn in review_turns(review):
        if str(turn.get("speaker") or "").strip() != speaker_id:
            continue
        if turn_speaker_key(turn) != dominant_key:
            continue
        if str(turn.get("human_label") or "").strip() == label:
            continue
        turn["human_label"] = label
        turn["edited"] = True
        changed += 1
    if changed:
        record_edit(review, "set_speaker_all", speaker_id)
    return changed


def set_turn_times(review: dict[str, Any], turn_id: str, start: float, end: float) -> None:
    if start < 0:
        raise ValueError("O tempo inicial nao pode ser negativo.")
    if end <= start:
        raise ValueError("O tempo final precisa ser maior que o tempo inicial.")
    turn = review_turns(review)[find_turn_index(review, turn_id)]
    turn["start"] = round(float(start), 3)
    turn["end"] = round(float(end), 3)
    turn["edited"] = True
    record_edit(review, "set_times", turn_id)


def toggle_turn_flag(review: dict[str, Any], turn_id: str, flag: str) -> None:
    turn = review_turns(review)[find_turn_index(review, turn_id)]
    flags = set(str(item) for item in turn.get("flags", []))
    if flag in flags:
        flags.remove(flag)
    else:
        flags.add(flag)
    turn["flags"] = sorted(flags)
    turn["edited"] = True
    record_edit(review, "toggle_flag", turn_id)


def set_turn_flags(review: dict[str, Any], turn_id: str, flags: list[str]) -> None:
    turn = review_turns(review)[find_turn_index(review, turn_id)]
    turn["flags"] = sorted({str(flag).strip() for flag in flags if str(flag).strip()})
    turn["edited"] = True
    record_edit(review, "set_flags", turn_id)


def _rotulo_visivel(turn: dict[str, Any]) -> str:
    return str(turn.get("human_label") or turn.get("speaker") or "")


def split_fits(texto: str, posicao: int | None) -> bool:
    """A posicao do cursor produz duas metades com conteudo? (puro)

    Quando nao produz — cursor na ponta, ou texto sem espaco util —, mover a
    fronteira significa passar o bloco INTEIRO para o vizinho, que e um caso
    real: um bloco curto atribuido todo ao falante errado."""
    if posicao is None or not 0 < posicao < len(texto):
        return False
    return bool(texto[:posicao].strip() and texto[posicao:].strip())


def _mover_fronteira(review: dict[str, Any], turn_id: str, para_frente: bool,
                     split_char: int | None, split_time: float | None) -> str:
    """O texto de um lado do cursor pertence ao bloco VIZINHO (puro).

    Este e o conserto da fronteira mal colocada pela separacao automatica de
    vozes: um bloco atribuido a A que, a partir de certo ponto, e de B. Fazia-se
    em cinco gestos (dividir, abrir o seletor, escolher B, juntar — e o juntar
    so passava depois de trocar o falante). Aqui e um so.

    O falante NAO e perguntado: ele e herdado do bloco vizinho, que ja tem o
    certo. E por isso que a operacao serve igual a uma entrevista a dois e a um
    grupo focal, onde "o outro falante" seria ambiguo.
    """
    turns = review_turns(review)
    index = find_turn_index(review, turn_id)
    vizinho_index = index + 1 if para_frente else index - 1
    if vizinho_index < 0:
        raise ValueError("Este é o primeiro bloco: não há bloco anterior para receber a fala.")
    if vizinho_index >= len(turns):
        raise ValueError("Este é o último bloco: não há bloco seguinte para receber a fala.")
    atual, vizinho = turns[index], turns[vizinho_index]
    if turn_speaker_key(atual) == turn_speaker_key(vizinho):
        raise ValueError("O bloco vizinho já é do mesmo falante — junte os dois.")

    edits = review.setdefault("edits", [])
    marca = len(edits)
    rotulo = _rotulo_visivel(vizinho)
    texto = str(atual.get("text", "")).strip()

    if split_fits(texto, split_char):
        novo_id = split_turn(review, turn_id, split_time=split_time, split_char=split_char)
        # Para frente, o pedaco que migra e o da DIREITA (o novo); para tras, o
        # da esquerda, que conserva o id original.
        alvo = novo_id if para_frente else turn_id
    else:
        alvo = turn_id          # o bloco inteiro passa

    set_turn_speaker_label(review, alvo, rotulo)
    sobrevivente = (merge_turn_with_next(review, alvo) if para_frente
                    else merge_turn_with_previous(review, alvo))
    # Um gesto do usuario deixa UM registro de edicao, nao os tres ou quatro
    # das operacoes internas.
    del edits[marca:]
    record_edit(review, "move_boundary", sobrevivente)
    return sobrevivente


def move_tail_to_next(review: dict[str, Any], turn_id: str, split_char: int | None = None,
                      split_time: float | None = None) -> str:
    """O texto A PARTIR do cursor pertence ao bloco SEGUINTE."""
    return _mover_fronteira(review, turn_id, True, split_char, split_time)


def move_head_to_previous(review: dict[str, Any], turn_id: str, split_char: int | None = None,
                          split_time: float | None = None) -> str:
    """O texto ATE o cursor pertence ao bloco ANTERIOR."""
    return _mover_fronteira(review, turn_id, False, split_char, split_time)


def merge_turn_with_previous(review: dict[str, Any], turn_id: str) -> str:
    """Junta este bloco com o ANTERIOR; devolve o id do bloco que sobrou.

    Pedido do usuario (2026-09-04): so existia juntar com o proximo, e o
    contorno — clicar no bloco de cima e juntar para a frente — troca o bloco
    aberto e perde o cursor. Nao ha logica de fusao nova aqui: e o mesmo
    caminho ja testado, aplicado ao bloco anterior."""
    turns = review_turns(review)
    index = find_turn_index(review, turn_id)
    if index <= 0:
        raise ValueError("Este é o primeiro bloco: não há bloco anterior para juntar.")
    return merge_turn_with_next(review, str(turns[index - 1].get("id")))


def merge_turn_with_next(review: dict[str, Any], turn_id: str) -> str:
    turns = review_turns(review)
    index = find_turn_index(review, turn_id)
    if index >= len(turns) - 1:
        raise ValueError("Cannot merge the last turn with a next turn.")
    current = turns[index]
    following = turns.pop(index + 1)
    if turn_speaker_key(current) != turn_speaker_key(following):
        turns.insert(index + 1, following)
        raise ValueError("Não é possível juntar blocos de falantes diferentes. "
                         "Troque o falante primeiro, se essa for a correção desejada.")
    current["start"] = min(float(current.get("start", 0) or 0), float(following.get("start", 0) or 0))
    current["end"] = max(float(current.get("end", 0) or 0), float(following.get("end", 0) or 0))
    current["text"] = " ".join([str(current.get("text", "")).strip(), str(following.get("text", "")).strip()]).strip()
    current["flags"] = sorted(set(current.get("flags", [])) | set(following.get("flags", [])))
    current["notes"] = " ".join([str(current.get("notes", "")).strip(), str(following.get("notes", "")).strip()]).strip()
    current["edited"] = True
    record_edit(review, "merge_next", str(current["id"]))
    return str(current["id"])


def _marcador_de_fronteira() -> str:
    """Marcador da nota da verificacao acustica de trocas de falante.

    Import tardio: `boundary_check` puxa numpy, e `review_store` e um modulo
    leve, usado em contextos que nao precisam dele. Se por algum motivo nao
    carregar, as marcacoes simplesmente nao migram — o pedaco da direita nasce
    limpo, que ja e melhor do que duplicar."""
    try:
        from .boundary_check import BOUNDARY_NOTE_MARKER

        return BOUNDARY_NOTE_MARKER
    except Exception:  # noqa: BLE001 - dividir um bloco nunca pode falhar por isto
        return ""


def _repartir_marcacoes(esquerda: dict[str, Any], direita: dict[str, Any]) -> None:
    """Ao dividir, as marcacoes NAO se duplicam (puro sobre os dois turnos).

    `split_turn` monta o bloco novo com deepcopy, entao flags e notas vinham
    junto: dividir um bloco marcado pela verificacao acustica criava dois
    blocos marcados, com a mesma nota, e a faixa 🔍 contava dois onde havia um.

    A nota de FRONTEIRA acompanha o pedaco da DIREITA — a suspeita sempre foi
    sobre a emenda com o bloco seguinte, e e a direita que passa a fazer essa
    emenda. Todo o resto (inaudivel, sobreposicao, duvida posta a mao, notas
    humanas) fica na esquerda, onde o usuario a pos."""
    marcador = _marcador_de_fronteira()
    notas = str(esquerda.get("notes") or "")
    flags = [str(f) for f in (esquerda.get("flags") or [])]
    if marcador and marcador in notas:
        direita["flags"] = ["duvida"] if "duvida" in flags else []
        direita["notes"] = notas
        esquerda["flags"] = [f for f in flags if f != "duvida"]
        esquerda["notes"] = ""
    else:
        direita["flags"] = []
        direita["notes"] = ""


def split_turn(review: dict[str, Any], turn_id: str, split_time: float | None = None, split_char: int | None = None) -> str:
    turns = review_turns(review)
    index = find_turn_index(review, turn_id)
    current = turns[index]
    text = str(current.get("text", "")).strip()
    split_char = choose_split_char(text, split_char)
    left_text = text[:split_char].strip()
    right_text = text[split_char:].strip()
    if not left_text or not right_text:
        raise ValueError("Choose a split point inside the text.")

    start = float(current.get("start", 0) or 0)
    end = float(current.get("end", start) or start)
    if split_time is None or split_time <= start or split_time >= end:
        split_time = start + ((end - start) * (split_char / max(1, len(text))))

    next_id = next_turn_id(turns)
    new_turn = deepcopy(current)
    _repartir_marcacoes(current, new_turn)
    current["end"] = round(float(split_time), 3)
    current["text"] = left_text
    current["edited"] = True
    new_turn["id"] = next_id
    new_turn["start"] = round(float(split_time), 3)
    new_turn["text"] = right_text
    new_turn["edited"] = True
    turns.insert(index + 1, new_turn)
    record_edit(review, "split", str(current["id"]))
    return next_id


def choose_split_char(text: str, requested: int | None) -> int:
    if requested is not None and 0 < requested < len(text):
        return requested
    midpoint = len(text) // 2
    left = text.rfind(" ", 0, midpoint)
    right = text.find(" ", midpoint)
    if left <= 0 and right <= 0:
        return midpoint
    if left <= 0:
        return right
    if right <= 0:
        return left
    return left if midpoint - left <= right - midpoint else right


def next_turn_id(turns: list[dict[str, Any]]) -> str:
    highest = 0
    for turn in turns:
        match = TURN_ID_RE.match(str(turn.get("id", "")))
        if match:
            highest = max(highest, int(match.group(1)))
    return f"turn_{highest + 1:06d}"


def turn_speaker_key(turn: dict[str, Any]) -> str:
    label = str(turn.get("human_label") or "").strip()
    if label:
        return " ".join(label.split()).upper()
    return str(turn.get("speaker", "SPEAKER_UNKNOWN"))


def record_edit(review: dict[str, Any], action: str, turn_id: str) -> None:
    review.setdefault("edits", []).append({"at": now_utc(), "action": action, "turn_id": turn_id})
