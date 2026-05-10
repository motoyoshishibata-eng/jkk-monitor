# ログイン調査 第2段 (2026-05-10T22:30:44.146652)

## cz9 (物件あり) URL: `https://jhomes.to-kousya.or.jp/search/jkknet/service/akiyaChizuRef`
- title: `JKKねっと > あき家検索・申込`

## 詳細ボタンクリック
- URL: `https://jhomes.to-kousya.or.jp/search/jkknet/service/akiyaSenDet`
- title: `JKKねっと > あき家検索・申込 > 先着順あき家募集`

### 詳細ページのリンク・ボタン抜粋
```json
[
  {
    "tag": "A",
    "type": null,
    "text": "申込区分",
    "onclick": "function onclick(event) {\njavascript:openHelp('index9.html'); return false\n}",
    "href": "https://www.to-kousya.or.jp/mz_help/index9.html"
  }
]
```

## 申込手続き or 申込 をクリック試行
- 試行: `a:has-text("申込")`
- クリック失敗: Timeout 15000ms exceeded.
=========================== logs ===========================
waiting for navigation until 'load'
============================================================

- 試行: `img[alt*="申込"]`
- 遷移先URL: `https://jhomes.to-kousya.or.jp/search/jkknet/service/mskInitSenForCheck`
- 遷移先title: `JKKねっと > ログイン`

### 遷移先のフォーム
```json
[
  {
    "action": "https://jhomes.to-kousya.or.jp/search/jkknet/service/mskInitSenForCheck#",
    "method": "post",
    "name": "ninsyologinForm",
    "inputs": [
      {
        "tag": "INPUT",
        "type": "text",
        "name": "loginRM.loginM.userId",
        "id": "",
        "value": null
      },
      {
        "tag": "INPUT",
        "type": "password",
        "name": "loginRM.loginM.password",
        "id": "",
        "value": null
      },
      {
        "tag": "INPUT",
        "type": "hidden",
        "name": "abcde",
        "id": "",
        "value": "CD14794E99E8A986D20A6DD4CBE8AF1F"
      },
      {
        "tag": "INPUT",
        "type": "hidden",
        "name": "jklm",
        "id": "xyz",
        "value": null
      }
    ]
  }
]
```

### password input: `[{'name': 'loginRM.loginM.password', 'id': ''}]`
- ★ ログイン画面到達