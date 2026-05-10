"""JKKねっと認証情報を .env に保存する対話ツール。"""
from __future__ import annotations

import getpass
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
ENV = ROOT / ".env"


def _read_env() -> dict[str, str]:
    if not ENV.exists():
        return {}
    out: dict[str, str] = {}
    for line in ENV.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v
    return out


def _write_env(data: dict[str, str]) -> None:
    # 既存の.envをベースに、指定キーだけ上書きで書き直す
    existing_lines: list[str] = []
    if ENV.exists():
        existing_lines = ENV.read_text(encoding="utf-8").splitlines()

    seen: set[str] = set()
    new_lines: list[str] = []
    for line in existing_lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            new_lines.append(line)
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in data:
            new_lines.append(f"{key}={data[key]}")
            seen.add(key)
        else:
            new_lines.append(line)

    # 既存ファイルになかったキーを末尾に追記
    for key, value in data.items():
        if key not in seen:
            new_lines.append(f"{key}={value}")

    ENV.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


def main() -> int:
    print("=" * 60)
    print("JKKねっと認証情報の設定")
    print("=" * 60)
    print()
    print("入力した値は .env に保存されます（.gitignore済みでGitHubには上がりません）")
    print()

    user_id = input("JKK_USER_ID (ログインID): ").strip()
    if not user_id:
        print("中断しました（IDが空）")
        return 1

    print("(パスワードは入力中に画面表示されません)")
    pw = getpass.getpass("JKK_PASSWORD: ")
    if not pw:
        print("中断しました（パスワードが空）")
        return 1
    pw_confirm = getpass.getpass("もう一度パスワード: ")
    if pw != pw_confirm:
        print("パスワードが一致しません。中断しました。")
        return 1

    data = _read_env()
    data["JKK_USER_ID"] = user_id
    data["JKK_PASSWORD"] = pw
    _write_env(data)

    print()
    print(f"[OK] {ENV} に保存しました")
    print(f"  JKK_USER_ID = {user_id}")
    print(f"  JKK_PASSWORD = ******** ({len(pw)}文字)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
