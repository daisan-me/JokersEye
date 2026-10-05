# 引き継ぎメモ

## 現在の状態（1.1.0 / 2026-10-05）

- 1.0 の Windows 専用構成（C# + WebView2 の EXE と同梱 Python）を、Windows / macOS 共通の Python 構成へ置き換えた。
- `app/server.py`・`app/scraper.py` の業務ロジック、`web/` の画面、DBスキーマ、CSV形式、取得の間隔・停止条件は変更していない。
- 変更点: OS差を `app/platform_support.py` に集約、専用ウィンドウ `app/desktop.py`（pywebview）を追加、`launch.py` を追加、`/api/focus`（二重起動時に既存ウィンドウを前面へ）を追加。
- 旧 EXE 版はタグ `v1.0.0-windows-exe` に残している。

## 検証済み

- macOS: 既存の単体テスト（app / geometry / scraper）と `tests/test_platform.py`、`node tests/test_floor_plan.cjs`。
- macOS 実機: `python app/desktop.py --diagnostics` で接続表示・版数・台番号マップ310台の描画、二重起動の拒否、「アプリを終了」での正常終了。
- macOS 実機: 公開サイトの1ページ（`?kishu=all` へのリンクを含む日別レポート）を取得ブラウザー経由で取得し、`scraper.all_data_link` で解析できた。

## 未検証（Windows 実機で確認が必要）

- `python launch.py` の初回セットアップと起動（pywebview + pythonnet + WebView2）。
- 取得ブラウザー（`SourceBrowser`）でのスクレイプ一式。WebView2 では `performance...responseStatus` が取れるため 401/403/429 の停止判定も有効になる想定。
- タスクバーのアイコン／AppUserModelID（`ctypes` による best effort）。
- 1.0 のデータフォルダー（`%LOCALAPPDATA%\JokersEye`）を開いての既存データ引き継ぎ。
- GitHub Actions（`.github/workflows/test.yml`）で Windows のテストが通ること。

## 既知の差・注意

- pywebview の `evaluate_js` は内部で `eval` を使い、アプリの CSP（`script-src 'self'`）に拒否される。`desktop.py` では `run_js`（生実行）を使う。CSP は緩めていない。
- 取得ブラウザーは `private_mode=False` が必須（公開サイトの確認スクリプトが Cookie を使うため。ONだと空ページのまま）。
- macOS の WKWebView では HTTP ステータスが取れない場合があり、その場合は「不明」として扱う（本文の確認で判定）。
- pywebview は、ナビゲーションの取り消しや権限要求の拒否を 1.0 の WebView2 と同じ細かさでは制御できない。取得ブラウザーは `source_url_allowed` を通ったURLにしか移動しない。
- 1.0 は WebView2 のプロファイルをアプリ本体と取得用で分けていた。1.1 は同一プロセス内で共有する（オリジンは別）。
