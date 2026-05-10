# JKK東京 空き家自動監視・通知システム 仕様書

## 0. このドキュメントの位置づけ

Claude Code に渡して Python スクリプト一式を実装させるための要件定義書。
個人利用（自分の入居申込のため）を前提とし、JKKねっと規約および倫理的スクレイピング原則を遵守する。

---

## 1. 目的

JKK東京の先着順あき家物件を自動監視し、新規空き発生から最短でユーザーが申込画面に到達できる状態を作る。

**達成したいこと**
- 検出ラグ ≤ 5分
- 通知ラグ ≤ 5秒（プッシュ通知）
- 通知タップから申込確定画面まで ≤ 10秒（事前ログインセッション維持）
- 人間の作業は「申込確定ボタンの最終タップ」のみ

**達成しないこと（意図的に外すスコープ）**
- 申込確定の完全自動化は行わない（理由は §10 参照）
- 第三者向けの情報配信・サービス化はしない（規約第11条(4)違反）

---

## 2. システム構成

```
┌──────────────────────────────────────────────┐
│  GitHub Actions (cron 5分間隔) または VPS cron  │
└──────────────────────┬───────────────────────┘
                       │
                       ▼
        ┌──────────────────────────┐
        │  fetch_listings.py       │  ← JKKねっと検索エンドポイントを叩く
        └──────────┬───────────────┘
                   │
                   ▼
        ┌──────────────────────────┐
        │  diff_detector.py        │  ← state.json と比較
        └──────────┬───────────────┘
                   │
                   ▼  (新規物件あり)
        ┌──────────────────────────┐
        │  notifier.py             │  ← Discord / LINE / Telegram プッシュ
        └──────────┬───────────────┘
                   │
                   ▼  (ユーザーがタップ)
        ┌──────────────────────────┐
        │  quick_apply.py (常駐)    │  ← Playwright で事前ログイン済みセッション維持
        │   申込画面まで自動遷移      │
        └──────────────────────────┘
```

---

## 3. 技術スタック

| 領域 | 採用 | 理由 |
|---|---|---|
| 言語 | Python 3.11+ | 汎用性・ライブラリ充実 |
| HTTP取得 | `httpx`（非同期対応） | `requests` より高速・タイムアウト制御が楽 |
| HTMLパース | `selectolax` または `BeautifulSoup4` | `selectolax` は高速、JKKは軽量HTMLなのでどちらでも可 |
| ブラウザ自動化 | `playwright` (chromium) | 申込画面までの自動遷移用、ログインセッション保持 |
| スケジューラ | GitHub Actions `schedule` または `cron` | 無料・運用が楽 |
| 通知 | Discord Webhook（第一候補）／ LINE Messaging API ／ Telegram Bot | Discord Webhook が最も実装が簡単で即時性高 |
| 状態管理 | `state.json`（ローカルファイル）または SQLite | 物件IDの履歴管理 |
| 設定 | `.env`（python-dotenv） | 認証情報の分離 |
| ロギング | `structlog` または標準 `logging` | 障害解析用 |

---

## 4. 機能要件

### 4.1 監視（Polling）

- **ポーリング間隔**: デフォルト5分（設定で1〜30分の範囲で変更可能）
- **対象URL**: JKKねっと あき家検索 `https://jhomes.to-kousya.or.jp/search/jkknet/service/akiyaJyoukenStartInit`
  - 実際の検索結果取得には POST リクエストが必要。Playwright で初回手動操作してネットワークタブから endpoint・パラメータを抽出するのが確実。
- **検索条件フィルタ**: `config.yaml` で指定可能にする
  - 区市町村（複数選択可）
  - 家賃上限（円）
  - 間取り（1K, 1DK, 2DK, 2LDK, 3LDK 等の複数選択）
  - 専有面積下限（m²）
- **User-Agent**: 一般的なブラウザのUAを設定（自動化検出回避ではなく、サーバー側ログ識別のため明示）
- **リトライ**: 失敗時は exponential backoff で最大3回（1分→2分→4分）
- **タイムアウト**: 30秒

### 4.2 差分検出

- **物件の一意キー**: `住宅名 + 部屋番号 + 家賃` のハッシュ（住宅IDが取得できればそれを優先）
- **state.json の構造**:
  ```json
  {
    "last_checked_at": "2026-05-10T14:30:00+09:00",
    "known_listings": {
      "<hash_id>": {
        "name": "○○ハイツ",
        "room": "201",
        "rent": 85000,
        "address": "港区△△",
        "first_seen_at": "2026-05-10T14:30:00+09:00",
        "url": "https://jhomes.to-kousya.or.jp/..."
      }
    }
  }
  ```
- **新規判定**: 前回の `known_listings` に存在しないキー = 新規物件 = 通知対象
- **消失判定**: 前回あって今回ないキー = 申込済または取下げ → 履歴は残すが通知不要

### 4.3 通知

**第一候補: Discord Webhook**
- 実装が最も簡単（Webhook URLを叩くだけ、認証不要）
- スマホアプリでプッシュ通知が即時届く
- メッセージにリッチエンベッド（画像・URL・物件情報）を埋められる

**通知メッセージ例**:
```
🏠 新規あき家検出
住宅名: ○○ハイツ
部屋: 201号室
家賃: ¥85,000
住所: 港区△△
間取り: 2DK / 45m²
発見時刻: 14:30:15
[👉 申込画面を開く](https://jhomes.to-kousya.or.jp/...)
```

**代替候補**:
- LINE Messaging API（Bot作成が必要だが日本ユーザーには馴染みやすい）
- Telegram Bot（最速・グローバル）
- Pushover（有料 $5 一回払い、信頼性高）
- ntfy.sh（無料セルフホスト可）

**通知先は複数併用可**にする（Discord失敗時にLINE、など）。

### 4.4 申込支援（クイック申込）

最も重要なパート。通知を受け取った瞬間から、ユーザーが申込画面の「申込確定」ボタンに辿り着くまでの時間を最小化する。

**戦略**:
1. **事前ログインセッションの維持**
   - Playwright を常駐モード（headless=False, persistent context）で起動しっぱなしにする
   - JKKねっとのログイン状態を Cookie 含めて保持
   - もしくは `storage_state.json` にCookieを保存し、新規ブラウザ起動時にロード
2. **通知URLからの直接遷移**
   - Discord通知のURLは「物件詳細ページ」を直接指す
   - スマホでタップ → ブラウザ起動 → ログイン済みセッション → 物件詳細表示
3. **ローカル常駐モード（オプション）**
   - PC を常時起動できる場合：Playwright が新規物件検出と同時に物件詳細ページを開いて申込フォームまで自動入力
   - ユーザーは申込ボタンを押すだけ
4. **申込フォーム自動入力**
   - 世帯情報・収入情報など、JKKねっとが要求する申込審査情報を事前に `.env` に保存
   - `quick_apply.py` が新規物件検出時に申込フォームまで自動入力
   - **最終的な「申込する」ボタンは押さない**（§10 参照）

**申込画面までの遷移ステップ自動化**:
```
1. ログイン（Cookie維持済みならスキップ）
2. 物件検索 → 該当物件クリック
3. 「申込手続きへ」クリック
4. 申込資格確認チェックボックス → 自動チェック
5. 世帯情報フォーム → 自動入力
6. 確認画面表示 → ここで停止し、ユーザーに最終確認を促す
```

---

## 5. ディレクトリ構成

```
jkk_monitor/
├── README.md
├── pyproject.toml          # uv または poetry
├── .env.example
├── .gitignore
├── config.yaml             # 検索条件・通知先設定
├── src/
│   ├── __init__.py
│   ├── main.py             # エントリポイント
│   ├── fetcher.py          # JKKねっと検索リクエスト
│   ├── parser.py           # HTMLパース → Listing オブジェクト
│   ├── diff.py             # state.json 比較
│   ├── notifier/
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── discord.py
│   │   ├── line.py
│   │   └── telegram.py
│   ├── auto_apply.py       # Playwright 自動遷移
│   ├── models.py           # Pydantic モデル定義
│   └── storage.py          # state.json 読み書き
├── tests/
│   ├── test_parser.py
│   ├── test_diff.py
│   └── fixtures/
│       └── sample_response.html
├── data/
│   ├── state.json          # gitignore 対象
│   └── storage_state.json  # Playwright Cookie保存（gitignore）
└── .github/
    └── workflows/
        └── monitor.yml     # GitHub Actions cron
```

---

## 6. 設定ファイル

### 6.1 `.env`
```
# JKKねっと認証情報
JKK_USER_ID=
JKK_PASSWORD=

# 通知先（使うものだけ設定）
DISCORD_WEBHOOK_URL=
LINE_CHANNEL_ACCESS_TOKEN=
LINE_USER_ID=
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=

# 申込フォーム自動入力用（任意）
APPLICANT_NAME=
APPLICANT_BIRTHDATE=
APPLICANT_INCOME=
HOUSEHOLD_SIZE=
```

### 6.2 `config.yaml`
```yaml
monitor:
  poll_interval_minutes: 5
  user_agent: "Mozilla/5.0 ..."
  timeout_seconds: 30
  max_retries: 3

filters:
  areas:
    - 港区
    - 渋谷区
    - 新宿区
  rent_max: 200000
  layouts:
    - 2DK
    - 2LDK
    - 3LDK
  area_min_m2: 40

notifications:
  discord:
    enabled: true
  line:
    enabled: false
  telegram:
    enabled: false

auto_apply:
  enabled: true
  mode: "prefill_only"  # prefill_only | full_auto（full_autoは§10の警告参照）
  open_in_browser: true
```

---

## 7. 実装フェーズ

**Phase 1: MVP（監視＋通知）— 最優先**
- [ ] JKKねっと検索リクエストの構造解析（Playwright で1回手動操作してDevToolsでendpoint特定）
- [ ] `fetcher.py` で物件一覧を取得
- [ ] `parser.py` で物件オブジェクト化
- [ ] `diff.py` で state.json と比較
- [ ] Discord Webhook 通知
- [ ] GitHub Actions cron 5分間隔で実行
- ✅ 完了基準: 新規空き家発生から5分以内にスマホに通知が届く

**Phase 2: 申込画面遷移自動化**
- [ ] Playwright で JKKねっとログイン
- [ ] storage_state.json でCookie永続化
- [ ] 通知URLから物件詳細→申込フォームまでの自動遷移スクリプト
- [ ] 世帯情報フォームの自動入力（`.env` から）
- ✅ 完了基準: 通知タップから申込確認画面まで10秒以内

**Phase 3: 運用改善**
- [ ] エラー監視（Sentry または Discord に障害通知）
- [ ] ログイン切れ自動再ログイン
- [ ] HTML構造変化の検出（パース失敗時アラート）
- [ ] 物件種別フィルタ追加（DIY住宅、若年夫婦向けなど）

---

## 8. エラーハンドリング

| 事象 | 対応 |
|---|---|
| HTTP 5xx | exponential backoff でリトライ |
| HTTP 429 (Too Many Requests) | ポーリング間隔を自動延長（5min → 15min） |
| HTML構造変化（パース失敗） | Discord にアラート送信、state.json は更新しない |
| ログインセッション切れ | 自動再ログイン、失敗時はアラート |
| Discord Webhook 失敗 | 代替通知先（LINE/Telegram）にフォールバック |
| state.json 破損 | バックアップから復元、なければ全件「既知」として再構築（次回から差分検出） |

---

## 9. JKKねっと検索エンドポイントの調査手順

JKKねっとの検索フォームは画面遷移ベースで、URLパラメータが暗号化（？）されているように見える。実装前に以下の手順で実際のリクエスト構造を確認する：

1. Playwright を `headed` モードで起動
2. https://jhomes.to-kousya.or.jp/search/jkknet/service/akiyaJyoukenStartInit にアクセス
3. DevTools の Network タブを開いた状態で検索条件を入力→検索実行
4. 発生するリクエスト（エンドポイント、メソッド、ヘッダ、ボディ）を全て記録
5. レスポンスHTMLを `tests/fixtures/sample_response.html` に保存
6. このHTMLを基に `parser.py` を実装

→ Claude Code は最初にこの調査ステップを Playwright スクリプトとして自動実行し、結果を `docs/endpoint_analysis.md` に記録すること。

---

## 10. 重要：申込確定の自動化について

**`auto_apply.mode` はデフォルト `prefill_only` とし、`full_auto` は推奨しない。**

理由：

1. **規約リスク**: JKKねっと規約第6条(6) により、過去30日間で2回キャンセルすると次の申込に制限がかかる。誤申込が連続するとペナルティ。
2. **資格審査リスク**: 申込資格を満たさない物件に自動申込すると規約第8条(虚偽・不正)に抵触する可能性。
3. **金銭的拘束**: 申込確定後の取消はマイページから可能だが、内見後のキャンセルは制限あり。
4. **代行禁止条項**: 規約第6条(1) は代行業者による申込みを禁止。本人の自動化ツールは代行業者ではないが、グレーゾーン。

**推奨運用**:
- 通知を受けたら即座に申込画面まで自動遷移
- フォーム入力も自動化
- **「申込する」ボタンの最終クリックだけはユーザーが行う**
- これにより通知から申込確定まで実測10〜15秒程度に圧縮可能

`full_auto` モードを実装する場合は明示的なオプトイン＋確認ダイアログ（CLI起動時に "I understand the risks" と入力させる等）必須。

---

## 11. 倫理・コンプライアンス

- ポーリング間隔は最低5分以上を厳守（サーバー負荷回避）
- 取得情報の第三者への共有・配信・販売は禁止（規約第11条(4)）
- 同居予定家族・同住所他者へのアカウント貸与不可（規約第4条(2)）
- 取得HTMLは個人解析目的のみで保持、長期間蓄積しない（30日でローテート）
- ログ・通知履歴に他のユーザーの個人情報が含まれないよう注意

---

## 12. 動作環境

- Python 3.11 以上
- OS: Linux（GitHub Actions Ubuntu）／ macOS ／ Windows いずれも可
- メモリ: 512MB 以上（Playwright 使用時は 1GB 推奨）
- ネットワーク: 安定したインターネット接続

---

## 13. 将来拡張の余地

- 都営住宅（抽選方式）の募集開始通知も統合
- 内見予約の自動化（規約上可能か要確認）
- 物件評価スコアリング（家賃/面積、駅距離、周辺環境）の自動付与
- 機械学習で「自分が好む物件」を学習し、優先度付け通知
- AVA Trade のEA運用と同じく、Slack/Discord にダッシュボードを集約

---

## 14. Claude Code への指示（実装開始時に渡す内容）

> このリポジトリで `jkk_monitor_spec.md` の Phase 1 を実装してください。
> まず §9 の調査ステップを Playwright で実行し、結果を `docs/endpoint_analysis.md` に記録してから本実装に入ってください。
> 各モジュールは pytest でテスト可能な形に分離し、`fixtures/sample_response.html` を使った単体テストを書いてください。
> 完了したら GitHub Actions の cron が動作することを確認し、README にセットアップ手順を記載してください。
