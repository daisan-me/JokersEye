# 引き継ぎメモ

## main と feature の統合（2026-10-06）

- ユーザー指示により、`feature/bbrb-scraper`（1.3.1）を main（Windows / macOS 共通構成）へ統合した。作業ブランチは `integrate/main-bbrb-scraper`。版数は1.3.1のまま。
- ユーザー判断：C# ランチャー（`launcher/Launcher.cs`・`launcher/SourceBrowser.cs`）は main の方針どおり削除し、feature で C# の取得ブラウザーに加えた変更を `app/desktop.py` へ移植した。EXE生成の `tools/build-windows-launcher.py` も削除した。
- 移植した内容：`web/source-readiness.js` による当日BB/RB表の待機判定（取得タスクの `kind`・`seats` を渡す）、認証・確認画面を検出したら操作・再試行せず停止、空ページは一度だけ通常再読み込みし、なお空なら後回し（取得失敗として返し、コレクターが再訪する）。
- `tests/test_juggler_math.cjs` の比較用Pythonを、削除された同梱 `runtime/python.exe` から `python3`（Windows は `python`、環境変数 `PYTHON` で変更可）に変更した。

### 検証済み（Linux）

- Pythonの単体テスト51件、Nodeのテスト（固定マップ、取得待機判定、ジャグラー準備、数値テスト992件・カバレッジ100%）。
- CI（`.github/workflows/test.yml`）に取得待機判定・ジャグラーのNodeテストを追加し、Node.jsの版を `.nvmrc` に合わせた。
- 移植した待機判定スクリプトを、`app/desktop.py` と同じ組み立て方でChromiumに注入し、BB/RB表の待機・認証画面の検出が動くこと。

### 未検証

- Windows / macOS 実機の pywebview 取得ブラウザーでの BB・RB 取得一式（WebView2・WKWebView の `run_js` で待機判定スクリプトが動くこと）。
- 上の「Windows / macOS 共通構成」の未検証項目は引き続き未確認。

## バージョン機能（2026-10-06）

- ユーザー指示：複数ブランチの版の確認が煩雑なため、アプリ内で版を選んで起動できるようにする。ユーザー判断：アプリ内で選んで起動する方式、試す版は本番データのコピーを使う。
- 本体は `app/versions.py`（一覧・ダウンロード・展開・起動・削除）と `web/versions.js`。APIは `/api/versions*`。対象は `daisan-me/JokersEye` の `package.yml` の成果物。
- 試す版は `JOKERS_EYE_TRIAL` 環境変数（その版の情報）付きで起動する。この変数がある版は試用中として表示し、ほかの版の操作とトークン登録を拒否する。
- GitHubの成果物ダウンロードは署名付きURLへのリダイレクトで返るため、トークンを付けずにそのURLから取得する（`download_address`）。
- 検証済み（Linux）：`tests/test_versions.py`（一覧の選び方、展開時のパス検査、トークンの保存と権限、試用中の制限、ダウンロード〜データコピー〜起動〜削除の流れ、API）。画面は偽の一覧で Chromium に表示して確認。
- 未検証：実際のGitHub（トークン登録・一覧・成果物のダウンロード）と、Windows / macOS 実機での起動。`package` ワークフローが一度成功して成果物ができるまで、一覧は空になる。

## コマンド不要の配布パッケージ（2026-10-06）

- ユーザーはコマンドラインを使わず、常に画面（GitHub・アプリ）で進捗と結果を確認したい。配布と確認はこの前提で設計する。
- `.github/workflows/package.yml` が Windows / macOS で PyInstaller パッケージを作り、`tools/smoke_test_package.py` で起動確認してから Artifacts に置く。ビルドは `tools/build_package.py`、ビルド用依存は `requirements-build.txt`。
- パッケージ内では `sys._MEIPASS` を ROOT とし、`web/`・`VERSION` を同梱する。Linux で `app/server.py` を同じ設定で固め、起動・版数の応答を確認済み。
- コード署名・公証は未対応。Windows の SmartScreen、macOS の Gatekeeper の確認画面が出る。

## Windows / macOS 共通構成（mainの旧1.1.0 / 2026-10-05）

- 1.0 の Windows 専用構成（C# + WebView2 の EXE と同梱 Python）を、Windows / macOS 共通の Python 構成へ置き換えた。
- `app/server.py`・`app/scraper.py` の業務ロジック、`web/` の画面、DBスキーマ、CSV形式、取得の間隔・停止条件は変更していない。
- 変更点: OS差を `app/platform_support.py` に集約、専用ウィンドウ `app/desktop.py`（pywebview）を追加、`launch.py` を追加、`/api/focus`（二重起動時に既存ウィンドウを前面へ）を追加。
- 旧 EXE 版はタグ `v1.0.0-windows-exe` に残している。

### 検証済み

- macOS: 既存の単体テスト（app / geometry / scraper）と `tests/test_platform.py`、`node tests/test_floor_plan.cjs`。
- macOS 実機: `python app/desktop.py --diagnostics` で接続表示・版数・台番号マップ310台の描画、二重起動の拒否、「アプリを終了」での正常終了。
- macOS 実機: 公開サイトの1ページ（`?kishu=all` へのリンクを含む日別レポート）を取得ブラウザー経由で取得し、`scraper.all_data_link` で解析できた。

### 未検証（Windows 実機で確認が必要）

- `python launch.py` の初回セットアップと起動（pywebview + pythonnet + WebView2）。
- 取得ブラウザー（`SourceBrowser`）でのスクレイプ一式。WebView2 では `performance...responseStatus` が取れるため 401/403/429 の停止判定も有効になる想定。
- タスクバーのアイコン／AppUserModelID（`ctypes` による best effort）。
- 1.0 のデータフォルダー（`%LOCALAPPDATA%\JokersEye`）を開いての既存データ引き継ぎ。
- GitHub Actions（`.github/workflows/test.yml`）で Windows のテストが通ること。

### 既知の差・注意

- pywebview の `evaluate_js` は内部で `eval` を使い、アプリの CSP（`script-src 'self'`）に拒否される。`desktop.py` では `run_js`（生実行）を使う。CSP は緩めていない。
- 取得ブラウザーは `private_mode=False` が必須（公開サイトの確認スクリプトが Cookie を使うため。ONだと空ページのまま）。
- macOS の WKWebView では HTTP ステータスが取れない場合があり、その場合は「不明」として扱う（本文の確認で判定）。
- pywebview は、ナビゲーションの取り消しや権限要求の拒否を 1.0 の WebView2 と同じ細かさでは制御できない。取得ブラウザーは `source_url_allowed` を通ったURLにしか移動しない。
- 1.0 は WebView2 のプロファイルをアプリ本体と取得用で分けていた。1.1 は同一プロセス内で共有する（オリジンは別）。

## 2026-10-05の完成範囲

- ユーザーの「実装途中のBB・RB取得機能を完成させ、1.3.1のブランチを更新する」という指示に従い、画面・API・CSV保存まで完成させた。表示版1.3.1を保持する。
- 作業ブランチは `feature/bbrb-scraper`。今後も機能ごとに作業ブランチを使う。
- ローカルの基点は `4fad665`（Windows EXE版1.0.0）。手元には固定マップ、基礎データシート、ジャグラー推測関数と検算など、表示版1.3.1までの変更があった。
- 同期時、GitHubのmainに `0ca6b03`（Windows/macOS共通Python構成）と `2620d64`（macOSアプリ生成）が存在することを確認した。`git pull --ff-only origin main` は未コミット変更の上書き防止で中止された。リモートmainは変更していない。
- （2026-10-06）mainのmacOS対応との統合は `integrate/main-bbrb-scraper` で行った。冒頭の「main と feature の統合」を参照。
- 新しいEXEや実データCSV・SQLiteはGitに追加しない。変更前EXE・本番SQLite・CSVをチャットのworkフォルダーへ退避してから、Windowsの既存インストールを更新した。

## BB・RB取得方式の確認結果

- みんレポ `https://min-repo.com/3389999/` の2026-10-03ゴッサムシティを取得した。
- 全台一覧から全310台の機種・ゲーム数・差枚・出率を取得し、72機種の機種別表と、必要な個別台表からBB・RB・合成を補完した。
- 全310台のBB・RBが揃い、うちジャグラーは9機種102台。0回も有効な実値として保持した。
- 1台設置のスマスロ鉄拳6（259番）とアニマルスロット ドッチ（264番）は、当日の基礎表と別のBB・RB表を組み合わせた。過去10日表を当日データとして読み取っていない。
- 機種別表では差枚・出率が「-」でも全台一覧には値がある場合がある。全台一覧の実値を保持する。
- 合成4台、出率5台は全台の元ページでも「-」のため空欄。推測値を補っていない。
- 指定の11列CSVを保存し、台番号1～310の重複なし・昇順、BB・RB全件あり、元データとの照合を確認した。作業データはリポジトリ外にあるため、必要なテスト資料は公開ページから機械的に小さなフィクスチャへ変換する。

## スクレイパー改修の完成内容

以前の保存点 `71feb81` で未接続だった以下の変更を、画面・API・CSV同期・ブラウザーまで接続した。

- 公開ページに存在する機種別／個別台リンクを保存する `scrape_bonus_targets` テーブル。
- 個別台の分離された当日BB・RB表の解析と、機種・ゲーム数・公開年月日の照合。
- BB・RBの実値による完了判定、保存済みグループのスキップ、失敗機種の再試行。
- 全台表のゲーム数・差枚・出率を再保存せず、BB・RB関連列だけ更新する処理。
- `bonus_only` モードと、取得ブラウザーに期待する台番号を渡す情報。

### 接続・検証済み

1. CSV初回作成・不足日更新・通常取得でBB/RBを標準取得。通常の本日更新は最新日の翌日以降だけで、過去の未掲載日へ戻らない。
2. `Store.build_base_sheet('bonuses')` は空のBB・RB・合成だけ同期。既存機種・G数・差枚・出率・非空欄を保持し、機種/G数が異なる記録を補完しない。取得終了・停止・失敗時もCSVへ同期し、同期が終わるまでactiveを維持する。
3. データ管理の「基礎データシート」に「BB・RBの不足だけ補完」、対象期間、停止、実値不足件数を追加。空欄の期間は保存済み全期間なので、多日数の実行は長時間を要する。
4. `web/source-readiness.js` を取得ブラウザー（統合前はC#、統合後は `app/desktop.py`）で使い、期待する全台の当日BB/RB表を待つ。HTTP 200の完全な空画面は通常再読み込みを一度だけ試し、戻らなければ後回し。公開レポートから再訪して個別台へフォールバックする。401/403/429・認証/確認画面は操作せず停止する。
5. 全角スペースを含む機種リンクの照合、個別台解析の変数衝突、モデル取得失敗で個別台フォールバックが飛ばされる問題を修正。0は実値、NULLは不足。古いcompleteマーカーで実値不足を隠さない。
6. Windows実取得で10月3日の全310台のBB/RBを取得し、ジャグラー102台も全件揃った。停止時96台を保持、残り214台だけで再開。11列CSVの10月3日310行とSQLiteの全項目一致、CSV全291,398行の日時/台番号順・重複なしを確認。同じ日を再実行するとup-to-date/不足0日0行で、公開ブラウザーログに新しい遷移がなかった。
7. 退避SQLiteとの比較で、既存レコードの機種・G数・差枚・出率・BB/RBの変更0件、新規は10月3日の310行だけ。実画面でWindows起動・ローカル接続・標準BB/RBチェック・補完ボタンを確認。日付を限定した実取得は同じローカルAPI経路で実行した。

初回CSV範囲は2024-03-01〜2026-09-30、更新は日本時間の本日まで。互換性のため旧CSVファイル名は保持するため、実際の最終日は画面で確認する。UTF-8 BOM、11列、日付/数値台番号順を保持する。

（統合で C# ランチャーと `tools/build-windows-launcher.py` は削除した。）読み取り専用実データ監査は `python tools/verify-bonus-sheet.py --data <data-folder> --day 2026-10-03`。

## その他の未完了作業

- `web/juggler-batch-core.js` と `web/juggler-batch-worker.js` は全ジャグラー解析の準備コード。画面・APIとの接続は未完成。
- `tools/audit_juggler_inputs.py` は保存済みBB・RB不足の読み取り専用監査。保存済みのBB・RBが少ないのは取得方式の不足であり、みんレポにデータがないことを意味しない。
- 過去期間のBB・RB一括補完はまだ実行していない。データ取得と設定推測は別処理として扱う。
- チェリーはユーザー指定の「チェリー狙いなし」を保持する。これはチェリー配当を0とする指示ではない。

## 検証

- 固定マップのNode検証に合格：310台の一律1.25倍表示、許可された5台だけの外向き調整、非重複、固定識別、91期間を確認。
- ジャグラー関数準備版の非自動実行チェックと、合成テストによる数値検算992件に合格。店舗の実データによる設定推測は実行していない。
- Python単体テスト42件合格。公開74ページの縮小資料 `tests/fixtures/min-repo-bonuses.json` で全310台・J102台を検証。API既定値、CSV不足だけ同期、取得済みモデルのスキップ、停止/再開、制限停止、矛盾データ保持を追加検証。`tests/testbonuses.py` はunittestが自動発見できる英小文字ファイル名。
- `node tests/test-source-readiness.cjs` に合格。当日表待機、平均・過去表除外、重複と部分表拒否、認証画面の検出、公開72機種の読み取り待機を検証。
- （統合前）C#ビルドに合格。単体テストは一時SQLiteを使用。実取得は退避後の本番SQLiteで10月3日のみ行い、従来データが不変であることを照合した。過去全期間のBB/RB一括補完と店舗データの設定推測は実行していない。
