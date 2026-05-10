# JKKねっと あき家検索エンドポイント解析（仕様書 §9）

調査日: 2026-05-10
対象: https://jhomes.to-kousya.or.jp/search/jkknet/

## 結論サマリ

- **ログイン不要** で検索画面・検索実行が可能。ログインが必要なのは申込手続きの段階。
- **小金井市の検索コード = `40`** （form field: `akiyaInitRM.akiyaRefM.checks`）
- 検索結果は**条件指定POST → 地図/結果ページ**の2段構え
- POST には3つのCSRFトークン (`token`, `abcde`, `jklm`) が必要 → 直前にGETで抽出
- **0件時と該当ありで返却ページ構造が異なる** → 判定ロジックが必要

## ページ遷移フロー

```
[1] GET  /search/jkknet/service/akiyaJyoukenStartInit
        → リダイレクトHTMLを返す。onload で popup window を開いて自分自身に POST
        ↓
[2] POST /search/jkknet/service/akiyaJyoukenStartInit
        body: redirect=true&url=https://.../akiyaJyoukenStartInit
        → 検索条件入力フォームのHTMLを返す（CSRFトークン入り）
        ↓
[3] POST /search/jkknet/service/akiyaChizuInitFromJyouken
        body: 検索条件 + token + abcde + jklm
        → 結果ページ（0件なら地図、該当ありなら物件一覧テーブル）
```

## 小金井市の検索コード

検索フォームの市区町村チェックボックス (`name="akiyaInitRM.akiyaRefM.checks"`) の value:

| 種別 | id | value 範囲 |
|---|---|---|
| 23区 | `ku` | `01` 〜 `23` |
| 市部 | `si` | `31` 〜 `66` (一部スキップあり) |

**小金井市 = `value="40"`**

他の代表値（参考、ラベルは parentElement.textContent から抽出）:
- `01`〜`23`: 23区（千代田～江戸川）
- `40`: 小金井市
- `46-47` のような範囲表記もあり（市町村合併の名残？）

完全な値↔市区町村のマッピングは `tests/fixtures/search_result_koganei.html` のラベルから抽出可能。

## 検索POSTのリクエスト仕様

**URL**: `POST https://jhomes.to-kousya.or.jp/search/jkknet/service/akiyaChizuInitFromJyouken`

**Content-Type**: `application/x-www-form-urlencoded`

**body（URLエンコード）**:

| フィールド | 値の例 | 説明 |
|---|---|---|
| `akiyaInitRM.akiyaRefM.checks` | `40` | 市区町村コード（複数可、繰り返し指定）|
| `akiyaInitRM.akiyaRefM.allCheck` | `` | 全選択フラグ |
| `akiyaInitRM.akiyaRefM.jyutakuKanaName` | `` | 住宅名（カナ部分一致） |
| `akiyaInitRM.akiyaRefM.ensenCd` | `` | 沿線コード |
| `akiyaInitRM.akiyaRefM.requiredTime` | `99` | 駅徒歩時間 (5/10/15/20/99=指定なし) |
| `akiyaInitRM.akiyaRefM.bus` | `1` | バス利用フラグ |
| `akiyaInitRM.akiyaRefM.madoris` | `1` 〜 `4` | 間取り（複数指定可） |
| `akiyaInitRM.akiyaRefM.yachinFrom` | `0` | 家賃下限（円） |
| `akiyaInitRM.akiyaRefM.yachinTo` | `999999999` | 家賃上限（円） |
| `akiyaInitRM.akiyaRefM.mensekiFrom` | `0` | 専有面積下限 |
| `akiyaInitRM.akiyaRefM.mensekiTo` | `9999.99` | 専有面積上限 |
| `akiyaInitRM.akiyaRefM.kaisoFrom` / `kaisoTo` | `` | 階数下限/上限 |
| `akiyaInitRM.akiyaRefM.muki` | `` | 向き |
| `akiyaInitRM.akiyaRefM.jtkSbt` | `` | 住宅種別 |
| `akiyaInitRM.akiyaRefM.equips` | `` | 設備 |
| **`token`** | `87606860D6477EF0A8710728E277E57A` | **CSRF1（hidden input）** |
| **`abcde`** | `EF8F0627B5A8184A6BA3E8705A00F068` | **CSRF2（hidden input）** |
| **`jklm`** | `993C34E5A8B2C6BDE44859268D8C1DB7` | **CSRF3（hidden id="xyz"、JSが値設定）** |
| `sen_flg` | `1` | （詳細不明、固定値で問題なさそう） |

## CSRFトークンの取得方法

検索フォームHTML（[2]の返却）に hidden input として埋め込まれている:

```html
<input type="hidden" name="token" value="...">
<input type="hidden" name="abcde" value="...">
<input type="hidden" name="jklm" value="" id="xyz">
```

`jklm` は input value が空。実際の値はJSが各ボタン押下時に動的に設定する。検索ボタン用の値は HTML 内の `<script>` ブロックに `document.frmMain.xyz.value = "XXX..."` として埋め込まれている。

## 0件 vs 該当ありの判定

| 状態 | 応答ページの特徴 |
|---|---|
| 0件 | サイズ ≈ 39KB、Tokyo全体の地図画像のみ表示、`件が該当しました` 文字列**なし** |
| 該当あり | サイズ > 40KB、`X件が該当しました` 文字列を含み、物件一覧テーブルあり |

判定ロジック例:
```python
if "件が該当しました" in response_text:
    # 物件あり → テーブルをパース
else:
    # 0件
```

**現時点（2026-05-10）の小金井市は 0件**（地図ページ返却を確認）。

## 物件一覧テーブルの構造

該当ありの場合、結果ページに以下の列を持つテーブルが含まれる:

| 列名 | 内容 |
|---|---|
| 住宅外観 | サムネイル画像 |
| 住宅名 | 物件名 |
| 地域 | 市区町村 |
| 優先種別 | 優先入居種別 |
| 住宅種別 | 公社一般／DIY等 |
| 間取り | 例: 2DK |
| 床面積[m2] | 数値 |
| 家賃[円] | 数値 |
| 共益費[円] | 数値 |
| 募集戸数 | 数値 |
| ─ | 「詳細」リンク（物件詳細ページへの遷移ボタン）|

サンプル: `tests/fixtures/chizu_ref_cz9.html`（小平市等で3件該当）

## 重要なサーバ仕様メモ

- **HTML文字コード = Windows-31J (Shift-JIS)**。`httpx` で取得時は `response.encoding = "cp932"` 指定が必要。
- **popup window 構造**: 初期ページは `target="JKKnet"` で別windowを開いてリダイレクト。Playwright では `context.expect_page()` で popup を捕捉。
- **地図クリックの czNo パラメータは「クラスタ」（複数市区町村のグループ）**であり、個別市の指定ではない。条件検索（checks）と地図ナビ（czNo）は独立した経路。
- **JKKが取り扱う行政区域は限定的**。フォーム側面の文言: 「行政区名の記載が無い地域は公社賃貸住宅の取扱いがございません」。

## 実装方針

### Option A: Playwright ベースの fetcher（推奨）

メリット:
- popup window と CSRF/JS の取扱いが自動的に解決される
- HTML構造が変わっても比較的耐性が高い

デメリット:
- 起動コスト（〜2-3秒）、メモリ使用量大
- GitHub Actions ランナーで `playwright install chromium` が毎回必要（cacheで緩和可）

擬似コード:
```python
def fetch(areas: list[str]) -> list[Listing]:
    code_map = {"小金井市": "40", ...}  # 市区町村→コードのマップ
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(locale="ja-JP")
        page = ctx.new_page()
        with ctx.expect_page() as popup_info:
            page.goto(START_URL, wait_until="load")
        popup = popup_info.value
        for area in areas:
            popup.check(f'input[name="akiyaInitRM.akiyaRefM.checks"][value="{code_map[area]}"]')
        popup.click('a:has-text("検索")')
        popup.wait_for_load_state("networkidle")
        html = popup.content()
        browser.close()
        return parse_listings(html)
```

### Option B: httpx ベースの fetcher

メリット: 高速、軽量
デメリット: CSRF抽出ロジックを自前で実装する必要、JS内の `xyz` 値抽出が脆い

実装は Phase 3（運用改善）以降に最適化の余地として残す。

## 参考fixture

- `tests/fixtures/search_result_koganei.html` — 0件時の応答（小金井市指定）
- `tests/fixtures/chizu_ref_cz9.html` — 3件該当時の応答（小平市他）
- `tests/fixtures/chizu_ref_cz1.html` 〜 `cz11.html` — 各クラスタの応答サンプル

これらをベースに parser のテストを書く。
