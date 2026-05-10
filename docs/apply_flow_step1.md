# 申込フロー調査 step1 (2026-05-10T22:57:07.524910)

## 申込ボタン直後（ログイン状態なら申込み確認）
- URL: `https://jhomes.to-kousya.or.jp/search/jkknet/service/mskInitSenForCheck`
- title: `JKKねっと > 申込資格確認`
- forms:
```json
[
  {
    "name": "userHeaderForm",
    "action": "https://jhomes.to-kousya.or.jp/search/jkknet/service/mskInitSenForCheck#",
    "method": "post",
    "inputs": [
      {
        "tag": "INPUT",
        "type": "hidden",
        "name": "token",
        "id": null,
        "value": "9B6C00A2806E0B82E69A14B6B0003C86"
      },
      {
        "tag": "INPUT",
        "type": "hidden",
        "name": "abcde",
        "id": null,
        "value": "dummy"
      },
      {
        "tag": "INPUT",
        "type": "hidden",
        "name": "jklm",
        "id": "shoriIdKbn",
        "value": null
      }
    ]
  },
  {
    "name": "frmMain",
    "action": "https://jhomes.to-kousya.or.jp/search/jkknet/service/mskInitSenForCheck#",
    "method": "post",
    "inputs": [
      {
        "tag": "SELECT",
        "type": "select-one",
        "name": "mskInitRM.mskInitM.yusenObo",
        "id": "yusenOboSelect",
        "value": null
      },
      {
        "tag": "INPUT",
        "type": "hidden",
        "name": "token",
        "id": null,
        "value": "9B6C00A2806E0B82E69A14B6B0003C86"
      },
      {
        "tag": "INPUT",
        "type": "hidden",
        "name": "abcde",
        "id": null,
        "value": "769E7D35FDFFD05044484538DF2F7F37"
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
- available_nav_options:
```json
[
  {
    "text": "マイページへ",
    "alt": "マイページへ",
    "onclick": ""
  },
  {
    "text": "ログアウト",
    "alt": "ログアウト",
    "onclick": ""
  },
  {
    "text": "申込資格について",
    "alt": "申込資格について",
    "onclick": ""
  },
  {
    "text": "確認のうえ申込",
    "alt": "確認のうえ申込",
    "onclick": "javascript:submitPage();"
  },
  {
    "text": "確認のうえ申込",
    "alt": "確認のうえ申込",
    "onclick": "javascript:showMsgShikaku();"
  },
  {
    "text": "戻る",
    "alt": "戻る",
    "onclick": "javascript:backPage();"
  }
]
```
- nav_elements:
```json
[
  {
    "tag": "A",
    "type": null,
    "text": "",
    "alt": null,
    "onclick": "javascript:submitHome('6FCE056BFFC6E23CE2E28CF306239717'); return false",
    "href": "https://jhomes.to-kousya.or.jp/search/jkknet/service/mskInitSenForCheck"
  },
  {
    "tag": "IMG",
    "type": null,
    "text": "マイページへ",
    "alt": "マイページへ",
    "onclick": "",
    "href": null
  },
  {
    "tag": "A",
    "type": null,
    "text": "",
    "alt": null,
    "onclick": "javascript:submitLogout('5FEF31B1294F61B173591AFAE25B8297'); return false",
    "href": "https://jhomes.to-kousya.or.jp/search/jkknet/service/mskInitSenForCheck"
  },
  {
    "tag": "IMG",
    "type": null,
    "text": "ログアウト",
    "alt": "ログアウト",
    "onclick": "",
    "href": null
  },
  {
    "tag": "A",
    "type": null,
    "text": "",
    "alt": null,
    "onclick": "javascript:openShikaku(); return false",
    "href": "https://jhomes.to-kousya.or.jp/search/jkknet/service/mskInitSenForCheck"
  },
  {
    "tag": "IMG",
    "type": null,
    "text": "申込資格について",
    "alt": "申込資格について",
    "onclick": "",
    "href": null
  },
  {
    "tag": "IMG",
    "type": null,
    "text": "確認のうえ申込",
    "alt": "確認のうえ申込",
    "onclick": "javascript:submitPage();",
    "href": null
  },
  {
    "tag": "IMG",
    "type": null,
    "text": "確認のうえ申込",
    "alt": "確認のうえ申込",
    "onclick": "javascript:showMsgShikaku();",
    "href": null
  },
  {
    "tag": "IMG",
    "type": null,
    "text": "戻る",
    "alt": "戻る",
    "onclick": "javascript:backPage();",
    "href": null
  }
]
```
