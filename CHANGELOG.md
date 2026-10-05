# Changelog

## 1.1.0 — 2026-10-05

- Windows / macOS 共通の構成に変更。C# ランチャーと同梱 Windows 用 Python を廃止し、`launch.py` と pywebview の専用ウィンドウ（`app/desktop.py`）に置き換え。
- OS 差を `app/platform_support.py` に集約（データフォルダー、外部ブラウザー、エラー表示）。Windows のデータフォルダーは 1.0 と同じ。
- 二重起動時に既存ウィンドウを手前へ出す `/api/focus` を追加。
- 機能・画面・DB・CSV・取得条件は 1.0 と同じ。
- `.python-version`、`requirements.txt`、`.gitattributes`、`.editorconfig`、CI（Windows / macOS / Linux のテスト）、`docs/handoff.md` を追加。


## 1.0.0 — 2026-10-02

- 初回の非公開GitHubリポジトリ版。
- 台番号マップ／機種マップを四角いタイル式グリッドで提供。
- 期間タブ、日付検索、台番号別の機種履歴を搭載。
- ローカルCSV取込、スクレイピング、データ管理、バックアップを同梱。
