"""指示書 §11 の報告フォーマットで結果を書き出す。

各 Phase は PhaseReport を組み立てて `write()` を呼ぶだけ。
reports/phaseN_*.md に保存し、PREREGISTRATION.md の該当セクションに追記する。
"""

from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
PREREG_MD = ROOT / "PREREGISTRATION.md"

MARKER_BEGIN = "<!-- PHASE-RESULTS:BEGIN -->"
MARKER_END = "<!-- PHASE-RESULTS:END -->"


def _fmt(v, nd=3):
    if v is None:
        return "—"
    if isinstance(v, float):
        if v != v:
            return "n/a"
        return f"{v:.{nd}f}"
    return str(v)


@dataclass
class PhaseReport:
    phase: str                   # 例 "Phase 1"
    axis: str                    # 例 "FOMCサイクル時間"
    hypothesis: str = ""
    preregistered: str = ""
    n_tests: int = 0
    bonferroni_crit_t: float = float("nan")
    judgment_method: str = "n>=100 + |t|>=2 + PF>=1.3 + Bonferroni補正"
    permutation: str = ""
    result_after_cost: str = ""
    regime_breakdown: str = ""
    neighborhood: str = "未検証"
    verdict: str = "不合格"
    reason: str = ""
    byproduct: str = "なし"
    tables: list[tuple[str, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_markdown(self) -> str:
        lines = [
            f"## {self.phase}: {self.axis}",
            f"- 実行日: {_dt.date.today().isoformat()}",
            f"- 事前登録した仮説: {self.hypothesis}",
            f"- 事前登録した閾値・窓: {self.preregistered}",
            f"- 実施した検定回数: {self.n_tests}",
            f"- 判定方法: {self.judgment_method}",
            f"- 結果（コスト控除後）: {self.result_after_cost}",
            f"- 円安期 / 円高期: {self.regime_breakdown}",
            f"- 近傍安定性: {self.neighborhood}",
            (f"- パーミュテーション検定: {self.permutation}" if self.permutation else
             f"- Bonferroni補正後の判定: 臨界|t| = {_fmt(self.bonferroni_crit_t)}"
             f"（検定{self.n_tests}回）"),
            f"- 判定: **{self.verdict}**",
            f"- 理由（1行）: {self.reason}",
            f"- 副産物: {self.byproduct}",
        ]
        if self.notes:
            lines += ["", "### 注記"] + [f"- {n}" for n in self.notes]
        for title, body in self.tables:
            lines += ["", f"### {title}", "", "```", body.rstrip(), "```"]
        return "\n".join(lines) + "\n"

    def write(self, slug: str) -> Path:
        REPORTS.mkdir(parents=True, exist_ok=True)
        path = REPORTS / f"{slug}.md"
        path.write_text(self.to_markdown(), encoding="utf-8")
        _append_to_project(self.to_markdown(), self.phase)
        return path


def _append_to_project(block: str, phase: str) -> None:
    """PREREGISTRATION.md のマーカー内に追記する（同じ Phase の既存ブロックは差し替え）。

    ファイル名が PROJECT.md でないのは、既存EA開発リポジトリの PROJECT.md
    （§4決定表・CFTC検証結果・TASK_37切替ルール等）が唯一の真実の源であるべきで、
    同名ファイルが2つあると参照先を見失うため（指示書 v1.1 差分5）。

    マーカー内の「## 」見出しだけを Phase ブロックとして扱い、それ以外の
    地の文（未実行のときの案内など）は最初の追記で捨てる。
    """
    if not PREREG_MD.exists():
        return
    text = PREREG_MD.read_text(encoding="utf-8")
    if MARKER_BEGIN not in text or MARKER_END not in text:
        return
    head, rest = text.split(MARKER_BEGIN, 1)
    body, tail = rest.split(MARKER_END, 1)

    sections: list[tuple[str, str]] = []
    cur_title, cur_lines = None, []
    for line in body.splitlines():
        m = re.match(r"^## (.+)$", line)
        if m:
            if cur_title is not None:
                sections.append((cur_title, "\n".join(cur_lines)))
            cur_title, cur_lines = m.group(1), [line]
        elif cur_title is not None:
            cur_lines.append(line)
    if cur_title is not None:
        sections.append((cur_title, "\n".join(cur_lines)))

    kept = [b for t, b in sections if not t.startswith(f"{phase}:")]
    kept.append(block.strip("\n"))
    PREREG_MD.write_text(
        head + MARKER_BEGIN + "\n\n" + "\n\n".join(b.strip("\n") for b in kept)
        + "\n\n" + MARKER_END + tail,
        encoding="utf-8")
