# 引き継ぎメモ

## 停止事由の番号と赤の表示（2026-10-08、ブランチ `feature/stop-reason-codes`）

- ユーザー指示：停止事由に番号を振り、番号と事由を表示する。停止事由の欄は赤を基調にする。
- `STOP_REASON_CODES`（`app/scraper.py`）で1〜7を固定。`scrape_runs.stop_code` に保存し、起動時に既存の記録へ事由の文字から番号を付ける。`status()` と履歴は `stopCode`／`stop_code` を返す。
- 画面：取得パネルの `#scrape-stop-reason`（赤い枠・背景、赤い丸の番号、「停止事由 N：事由」と詳しいメッセージ）、履歴の表の停止事由列（赤い丸の番号と赤い文字）。色は `--red`・`--red-bg`・`--red-line`（暗い・明るいテーマ別）。表の関数 `table()` は `{html: ...}` のセルだけHTMLで出す。
- 検証：単体テスト（番号の重複なし・全事由に番号、記録と表示用の値、古い記録への付番）。画面は検証用データに停止記録を1件足して、暗い・明るいテーマで確認（サイトへのアクセスなし）。

## 取得の記録：停止事由・所要時間・1日あたり（2026-10-08、ブランチ `feature/run-records`）

- ユーザー指示：停止した場合は停止事由を書く、取得にかかった時間を記録する、1日あたりの時間を計算する。
- `scrape_runs` に `stop_reason`・`elapsed_seconds`・`days_done`・`seconds_per_day` を追加（既存DBは起動時に列を追加、古い実行の所要時間は開始・終了時刻から補う）。`stop_reason()` で分類し、`Collector.stop(reason)` は `button`（取得パネル）と `window`（取得用ブラウザーを閉じた）を区別する。起動時に残っていた `running` は「アプリの終了で中断」。
- `Collector.status()` は取得中に経過秒・処理日数・1日あたり・残り見込み秒を返す。表示は `web/scrape.js`（`#scrape-run`）と `web/app.js` の履歴表。
- 検証：単体テスト91件（完了時の記録、停止事由の分類、ボタン／窓／制限での停止、取得中の見込み、古い表の列追加と所要時間の補完）。画面は検証用データの過去の記録で確認（サイトへのアクセスはしていない）。

## 差枚・出率の欠け（サイトが値を伏せた）と11列の完全性チェック（2026-10-07、ブランチ `fix/complete-values`）

- ユーザー指摘：全期間の取得後、未取得（空欄）が多すぎる。BB・RBだけでなく11列すべてを埋めることを前提にすべきだった。
- 実態（本番 `Documents\jokers-eye-data`、2024-03-01以降 292,018行）：BB・RB欠け3行、合成の空欄17,206行（すべてBB+RB=0で正しい）、差枚の空欄173,786行・出率の空欄184,425行。空欄はすべて差枚がマイナスの台。
- 原因：みんレポのサーバーが、アプリの取得用ブラウザー（WebView2のプロファイル）にだけ、マイナスの差枚と出率を「-」にしたHTMLを返している。読み込み直後から「-」で、スクリプトの後書きではない。普通のブラウザー（Claudeのブラウザーペイン）では同じページに数字が出る。旧版（1枚・ゆっくり）で取った9/1は欠けていない。12枚の高速取得の開始以降に取った日だけ欠けているため、自動取得として判定された対策とみられる。取得に使っていない新しいプロファイルでは確認ページ（表なし）だった。対策のすり抜け（Cookie削除・別プロファイル等）はしない。
- 修正：`missing_values()` で遊技された台の差枚・出率の欠けを数え、全台表で `max(10, 行数の10%)` を超えたら `VALUES_HIDDEN` として保存せず全体を止める（`stops_everything`）。`scrape_value_days` に「全台表を読み直した日と残った欠け数」を記録し、読み直し済みの日は再取得しない。`base_complete` は差枚・出率の欠けがあり未確認なら偽にする。`Store.gdb_value_days()` を `gdb_range_plan` に加えた。取得用ブラウザーの `--host-resolver-rules`（広告などの遮断）は撤去。
- 検証：単体テスト（伏せられた表を保存せず全体停止、欠けた日の全台表だけの読み直しと再取得しないこと、BB/RBの保持）。検証用データの9/29で読み直したところ、まだ202台が「-」で、保存せず止まった（サイトの対策は継続中）。
- 次：mainに入れてアプリを更新し、「期間を指定して取得」（2024-03-01〜本日）で欠けた日だけを取り直す。対策が続いていれば最初の日で止まるので、時間をおいて再開する。

## 取得の速さの自動調整とサイトエラーでの停止（2026-10-07、ブランチ `fix/adaptive-scrape`）

- 事象：12枚・毎分240ページの版（`8277be7`）で、ユーザーが朝10時前後に取得したところ、みんレポがWordPressの「Error establishing a database connection」（タイトル Database Error、HTTP 500または200）を返し続けた。本番の `source-browser.log` で約4分半に171ページ中33ページ、最初のページのサーバー応答待ちは7.6秒。エージェントがすぐ `scrape/stop` で停止した。直後にサイトは復旧していたが応答待ちは1.6秒。前回の高速化の検証（午前3時、応答待ち0.2〜0.8秒）では問題がなかったため、混む時間帯にこちらの負荷が重なったと判断した。
- 修正：`Collector.adapt()` が窓からの `serverWait`（requestStart→responseStart）で同時に読む枚数 `parallel` を調整し、`browser_task` は読み中のページ数が `parallel` 未満のときだけ渡す。開始3・最小2・最大12、0.8秒未満が `parallel` 回続けば+1、2秒超で半分。`stops_everything()` で制限（`RESTRICTED`）とサイトのエラー（`SITE_TROUBLE`）をまとめて扱い、全窓を止める。窓は HTTP 500番台かDatabase Errorの画面をその場で失敗にする（55秒待たない）。
- 検証（Windows、混む時間帯の10時台）：1日分を81秒、同時枚数は2〜4枚で推移、BB/RB不足0・失敗0・Database Error 0。単体テストに、サイトエラーでの全停止、同時枚数の増減、Database Error画面での即時失敗を追加。

## 公開リポジトリへの移行（2026-10-07）

- 経緯：非公開リポジトリのGitHub Actionsが無料枠（月2,000分、Windowsは2倍・macOSは10倍で計算）を超え、「支払いの失敗か使用上限の引き上げが必要」としてジョブが起動しなくなった（10月の換算使用量は約4,720分で、9割以上がmacOS）。ユーザー判断でリポジトリを公開する。公開リポジトリでは通常の実行環境が無料。
- 公開前の確認：全履歴にトークン・パスワード・鍵、実データ（DB・CSV）、`.env` はなかった。旧Windows配布物（Python本体・WebView2の部品）は再配布可能なもの。問題は2点。
  - 過去2コミットの作者欄の個人メールアドレス → ユーザー判断で履歴を書き換え、GitHubの非公開用アドレス（`37851315+daisan-me@users.noreply.github.com`）に置き換えた。
  - DMMぱちタウン掲載の店舗フロア図 `web/floor-map.webp` → ユーザー判断で外し、履歴からも削除。アプリは位置記録の照合キーを `fixed-floor.json` のハッシュに変え、画面では掲載元ページへのリンクを表示する。
- GitHubはプルリクエストの中身（`refs/pull/*/head`）を消せないため、同じリポジトリでの書き換えでは消しきれない。ユーザー判断で、旧リポジトリを `daisan-me/JokersEye-private`（非公開のまま、旧PR #1〜#7と旧履歴を保管）に改名し、書き換えた履歴で新しい公開リポジトリ `daisan-me/JokersEye` を作成した（`git filter-repo --invert-paths --path web/floor-map.webp --mailmap`）。
- 影響：全コミットのハッシュが変わった（このメモ等に残る古いコミット番号は旧リポジトリのもの）。PR番号は新リポジトリで1から振り直し。ほかの端末（Mac等）の既存クローンは使わず、取り直すこと。手元（Windows）は `origin` を新リポジトリに切り替え、旧リポジトリは `private` として参照できる。

## 取得の並行化（2026-10-07、ブランチ `feature/parallel-scrape`）

- ユーザー指示：取得を毎分240ページまでにする。最初は「6窓で1ページ1.5秒以内」で実現する指示。6窓では1ページ約3秒（毎分約76）で届かず、ユーザー判断で「A（みんレポ以外への通信を止める）とB（窓を増やす）を同時に」。
- 事前確認：min-repo.com の robots.txt は Amazonbot の禁止と meta-externalagent の Crawl-delay 1 だけ。「当サイトについて」はデータ引用時のリンク掲載のお願いのみで、自動取得の禁止記述は見当たらない。これまでの記録に429・403・確認画面は0件。
- `app/scraper.py`：1件ずつの受け渡しを待ち行列にし、全体で0.25秒に1ページ以上は渡さない（`PAGES_PER_MINUTE = 240`）。渡したのに20秒応答がないページは渡し直す（`PAGE_LEASE`、遅れた応答は無視）。機種別ページのBB/RB取得は `ThreadPoolExecutor(BROWSER_WINDOWS=12)` で並行。制限が出たら `halted` を立て、ほかのページは開かずに止める。旧来の `pace`（1秒・3秒の待ち）は廃止。
- `app/desktop.py`：`SourceWindow`（1枚）と `SourceBrowser`（12枚を管理）に分割。4列×3段に並べ、各ウィンドウは前のページの開始から1.5秒あける。`WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS` に `--host-resolver-rules="MAP * ~NOTFOUND, EXCLUDE min-repo.com, EXCLUDE *.min-repo.com, EXCLUDE 127.0.0.1, EXCLUDE localhost"` を設定（pywebviewの既定引数も残す）。
- 速さの要点：pywebview（WinForms）の `run_js` はすべて1本のUIスレッドを通るため、12枚が0.1秒ごとに確認すると渋滞して1ページ数秒かかった（サーバー応答は約0.2〜0.8秒）。確認を1回の呼び出し（URL・状態・判定・HTML）にまとめ、`loaded` イベントまでは1秒に1回だけ確認する形にした。機種別ページでは「同じリンクをクリック」の確認も省く。ウィンドウの受け取り問い合わせの待ち上限は10秒（3秒だと混雑時に渡し損ねが出て75秒止まった）。
- `application.log` には受け渡し・状態の頻繁な問い合わせを記録しない。1.5秒を超えたページは `source-browser.log` に `slow-page`（サーバー待ち・受信・組み立ての内訳つき）で残す。
- 測定（Windows、検証用データ）：1枚の旧版 1日約6分 → 6窓 58.5秒 → 12窓＋通信制限＋確認削減 22.1秒/日（3日分222ページを66.3秒、毎分約201ページ、1.5秒超は24%、BB/RB不足0、失敗0）。全期間約950日で約6時間の見込み。サイトの混み具合で1日あたり30〜90秒にぶれることがある。
- macOS（WKWebView）では `--host-resolver-rules` は効かない（取得は動くが通信制限なし）。macOS実機での並行取得は未検証。

## 取得操作を1つのパネルにまとめる（2026-10-07、ブランチ `feature/single-scrape-panel`）

- ユーザーの設計：パネルで期間指定または本日までを取得 → GDB（SQLite）に保存 → 「登録した観測」で確認。「本日までの分を更新」はGDBに記録されている最後の日から今日まで。取得の操作が2つのパネルに分かれている意味はない。
- `web/scrape.js`：唯一の取得パネル。`POST /api/gdb/update`（本日まで）と `POST /api/gdb/range`（期間指定）を呼ぶ。BB・RBは常に取得。取得終了時に `state`・「登録した観測」を読み直し、`jokers:data-changed` イベントを出す。
- `web/gdb.js`：閲覧専用（取得ボタンを削除）。`jokers:data-changed` で状態と一覧を読み直す。
- `Store.gdb_today_plan()`：GDB_START以降の最後の記録日（なければGDB_START）から今日までの `gdb_range_plan`。最後の記録日より後の「未掲載」日は再確認する（当日のレポートは翌日に出るため）。`gdb_range_plan(..., retry_unpublished_after=)` を追加。
- 削除：`POST /api/scrape/start`（旧パネルの本日まで・指定範囲）、`POST /api/gdb/bonuses`、`Store.gdb_update_plan`。
- 検証（Windows）：Python単体テスト72件・Nodeテスト4本。検証用データ（10/3の310行）で起動し、パネルから2026-10-02を期間指定で取得：完了、BB/RB 310/310、失敗0、終了後にパネル・「登録した観測」（10/2が選択肢に追加）・GDBの一覧が自動で620行に更新された。「本日までの分を更新」は10/4〜10/7の4日を対象に開始することを確認し、すぐ停止した。

## データフォルダーの振り替え問題と置き場所の固定（2026-10-07）

- 発見：Claudeデスクトップアプリ内のシェル（エージェントのBash/PowerShell）と、そこから起動したプロセスは、`%LOCALAPPDATA%` への書き込みが `C:\Users\daisa\AppData\Local\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Local` に振り替えられる。エクスプローラー（Claudeの外）で見ると、本物の `C:\Users\daisa\AppData\Local\JokersEye` は存在しなかった。`AppData\Roaming`（スタートメニュー・タスクバーのショートカット）とドキュメント・ダウンロードは振り替えられなかった。
- 影響：2026-10-06〜07にエージェントが「本番データフォルダー」で行った操作（台データの削除、CSVの移動、GDBのCSVの作成・削除、DBの改名、バックアップ）は、すべてClaude専用の振り替え先で起きていた。2026-10-07 01:36にその振り替え先が作り直され、Claude内で起動されたmainの版が台データ318,368行のDBを置いていた。これを「ユーザーが作り直した」と判断したのは誤り。
- ユーザー判断：データはAppDataの外に固定する。台データは持っていかず空から始める。
- 実施：`C:\Users\daisa\Documents\jokers-eye-data` を作成。振り替え先のDB（318,368行）を `backups\GothamDataBase-claude-cache-20261007-020132-163633.sqlite` として保存（整合性確認済み）。mainの版（`00407f9`）を `Downloads\JokersEye-main-00407f9-windows` に置き、スタートメニュー・デスクトップ・タスクバーのショートカットを `--data "C:\Users\daisa\Documents\jokers-eye-data"` 付きでこの版に向けた。タスクバーのアイコンをクリック（Claudeの外での通常起動）して起動し、新しい置き場所に空のGDB（貸区分の列なし、店内マップ28,210件）ができたことを確認した。
- 残り：振り替え先 `Packages\Claude_…\LocalCache\Local\JokersEye` と、古い版の `Downloads\JokersEye-1.3.1-windows` は残している。

## 登録した観測の11列表示と貸区分の廃止（2026-10-07、ブランチ `feature/observation-columns`）

- ユーザー指示：「登録した観測」にもGDBと同じ11列（記入例：3/12 金 114 ～ 非ジャグラー 3000 12 13 1/120 970 107.3%）を表示する。貸区分は今後まったく扱わず、表示もDBからもなくす。
- `Store.observations(day)` は `scraped_observations` だけから `_gdb_row` の11列を返す（`columns` 付き）。画面（`web/app.js` の `observationCells`）で日付を「M/D」、曜日を1文字、系統を「ジャグラー／非ジャグラー」、空欄を「未取得」に整える。
- 貸区分の削除：`scraped_observations.rate`（`Collector.__init__`）と `installations.rate`（`Store._drop_lending_rate`）を `ALTER TABLE … DROP COLUMN` で削除。旧CSV取り込み用の `observations`・`imports` は空のときだけ削除（中身があれば残すが、アプリは読まない）。`state().summary` から `legacy`・`main` を外し、`withBonus`（BB・RBが両方ある記録数）を追加。
- 本番データフォルダーの状況（2026-10-07 01:36に作り直されていた）：`GothamDataBase.sqlite` に削除前の台データ318,368行が戻り、`backups` とログは無くなっていた。起動中のアプリはmainから作った版（`Downloads\JokersEye-1.3.1-windows`）。ユーザー側の操作と判断し、触れていない。
- 検証（Windows）：Python単体テスト72件・Nodeテスト4本。本番DBのコピー（318,368行・`rate` 列あり）で起動し、`rate` 列と空の旧表が消え、台データ318,368行・店内マップ28,210件が残ることを確認。画面の「登録した観測」が11列で表示され、どのページにも「貸」の表記がないことを確認。

## 期間を指定した取得（2026-10-07、ブランチ `feature/range-fetch`）

- ユーザー指示：期間を指定してデータを取得できる機能を付け、GitHubのブランチに上げ、Windows用のダウンロードURLをGitHubに貼る。背景：全期間（約950日・約73,000ページ）を長時間回したあとで取り方の誤りに気づくと検証と取り直しに時間がかかるため、短い期間で試してから広げたい。
- `Store.gdb_range_plan(start, end)`：範囲内で「記録がない日（既知の未掲載日は除く）」と「BBまたはRBが空の日」を合わせた日付を返す。`POST /api/gdb/range` はその日付だけを `Collector.start(..., include_bonus=True, only_dates=...)` に渡す（記録済みの日は既存行を使いBB/RBだけ補完）。開始日・終了日は必須で、2024-03-01〜本日。
- 画面：GDBカードに開始日・終了日と「期間を指定して取得」ボタン。データ管理上部の「初回の取得範囲を指定」（旧来の範囲取得）は残している。
- 検証（Windows）：Python単体テスト70件・Nodeテスト4本。空のGDBで修正版を起動し、画面から2026-10-03〜2026-10-03を指定して取得：完了、310台・BB/RB不足0、失敗0、CSVなし、取得ブラウザーは終了後に閉じた。日付未入力ではエラーを表示。
- 未決定：スクレイピングの高速化（案A〜E）。ユーザーは「同時に開く」可否を検討中。これまでの記録に429・403・確認画面は0件。

## GDB を SQLite 1ファイルにし、CSV を廃止（2026-10-07、ブランチ `feature/gotham-database`）

- ユーザー指示：CSVだけで管理する不都合（部分更新・同時アクセス・Excel保存での値化け・重複防止）を説明したうえで、ユーザーがSQLiteを選択。GDBのCSVは削除し、今後CSVは使わない。SQLiteのファイル名を `GothamDataBase.sqlite` にする。
- ユーザー判断：アプリのCSV機能はすべて外し、DBだけにする（一覧と3つの操作はDBを直接読む形で残す）。
- 変更内容：
  - `app/server.py`：DBファイルを `GothamDataBase.sqlite` に変更。旧 `jokers-eye.sqlite3`（と `-wal`・`-shm`）は起動時に一度だけ改名して引き継ぐ。バックアップは `backups/GothamDataBase-日時.sqlite`。別版の試用（`copy_into`）は、1.3.1以前の版も読めるよう旧名でコピーする（新しい版は起動時に改名）。
  - 基礎データシート（CSV）の作成・更新・同期・ダウンロードを廃止し、`gdb_rows_all`（DBから11列を作る。件数と最新 `fetched_at` が変わるまでキャッシュ）・`gdb_status`・`gdb_update_plan`・`gdb_bonus_plan`・`gdb_rows` に置き換え。APIは `/api/gdb/status|rows`（GET）、`/api/gdb/update|bonuses`（POST）。「CSVを作成」と「不足分だけ更新」は「不足日を取得」（2024-03-01〜本日でDBに記録がない日）にまとめた。
  - `app/scraper.py`：`exports` へのCSV自動書き出し（10日ごと・終了時）と、終了時の基礎データシート同期を削除。
  - CSV取り込み（`/api/import`・`web/template.csv`）、`/api/scrape/csv`、`tools/verify-bonus-sheet.py`、`tools/export_existing_facts.py` を削除。`tools/audit_juggler_inputs.py` はDBだけを読む。
  - 画面：`web/base-sheet.js` → `web/gdb.js`（カード名「GothamDataBase（GDB）」）。データ管理の「CSVを取り込む」を削除。履歴は取り込み履歴の代わりに取得の実行履歴（`scrape_runs`）を表示。設定にDBファイル名を表示。
  - `observations`・`imports` テーブルは既存DBとの互換のため残したが、書き込む機能はなくなった。
- 検証（Windows）：Python単体テスト67件・Nodeテスト4本に合格。本番DBのコピー（旧名）で起動し、`GothamDataBase.sqlite` への改名、店内マップ91期間の読み込み、データ管理・ダッシュボード・履歴・設定・マップの各画面にCSVの文言がないこと、GDBカードの表示（不足日951日）を確認した。
- 本番データフォルダー：見出し行だけの `GothamDataBase.csv` を削除した。`backups\exports-before-gdb-*` の旧CSVはバックアップとして残している。本番の `jokers-eye.sqlite3` は、この版を初めて起動したときに改名される。

## GDB（CSV）の作成と旧台データの削除（2026-10-07、ブランチ `feature/gotham-database`）

- ユーザー指示：台データは `GothamDataBase.csv`（GDB）1枚で扱う。旧台データは中身が使い物にならないので一度削除する。GDBは型（見出し行）だけ作り、スクレイピングで埋めるのは後で。開始日は2024-03-01のまま。ルールは `AGENTS.md` の「GDB」に記載。
- ユーザー判断：削除は「取得データとCSVだけ」、バックアップは残す。アプリのコードは変えず、GDBファイルを作るだけ。
- 本番データフォルダー（`%LOCALAPPDATA%\JokersEye`）で行ったこと：
  - 削除前のSQLiteを `backups\jokers-eye-before-gdb-20261007-000926-6969fd.sqlite3` に保存（整合性確認済み、取得データ318,368行）。
  - SQLiteの `scraped_observations`・`scrape_days`・`scrape_bonus_days`・`scrape_bonus_targets`・`scrape_report_index`・`scrape_runs` を空にしてVACUUM（72MB→4.6MB）。
  - 店内マップの `map_versions`・`seats`・`installations`（28,210件）と `settings` は残した（`web/map-history.json` から起動時に作り直される）。
  - `exports` のCSV4つ（`gotham-city-*`・`coverage-*`）を `backups\exports-before-gdb-20261007-000926-6969fd\` に移した。
  - 見出し行だけの `GothamDataBase.csv` を作成。
- （同日、上の「GDB を SQLite 1ファイルにし、CSV を廃止」でGDBのCSVは削除し、GDBはSQLiteになった。）

## スクレイピングの修正（2026-10-06、ブランチ `fix/scrape-completion`）

- ユーザー報告：Joker's Eye からスクレイピングしても、(1) 全日程のBB/RBが埋まらない、(2) いつまでも完了しない、(3) 完了しても取得用ブラウザーが閉じない。
- 原因(1)(2)：pywebview 6.2.1 の WebView2 実装は `get_current_url()` に `System.Uri.ToString()`（日本語に戻した形）を返す。`same_page` が生の query を比べていたため `?kishu=<日本語>` の機種別ページが常に不一致になり、各ページ110回×0.5秒待って `unreadable-page … loading` で失敗していた（本番の `source-browser.log` に51件）。1日あたり約70機種×2回＋個別台フォールバックで、1日分だけで数時間かかっていた。
- 修正：`SourceBrowser.current_url()` はページの `location.href` を優先し、`same_page` は `parse_qsl` で query を比較する。
- 追加原因：Cookie のない新しい取得用プロファイルで機種別ページを直接開くと、空の文書（HTTP 200・188文字・表0）が返る。補完（既存行を使う経路）では公開レポートを一度も開かずに機種別ページへ直行していた。`collect_bonuses` は、未完了の機種があり、この回で全台表を読んでいない場合、最初にその日の公開レポートを1回開く（以後の機種は既存どおり）。
- 原因(3)：取得ブラウザーを閉じる処理がなかった。`/api/scrape/browser-task` に `active` を加え、`active: false` でウィンドウを閉じる。ユーザーが取得中に閉じた場合は従来どおり隠して停止する。
- 10/3 のBB/RBが本番で63台しかないのは、10/3の営業中に取った値が、17:26の取得で最新の全台表（G数が変化）に置き換わり、規則どおりBB/RBが空に戻ったため。

### 検証済み（Windows 実機）

- Python 単体テスト69件、Node のテスト4本。
- 本番データフォルダーのコピー（SQLite は backup API で複製）で修正版 `app/desktop.py` を起動し、10/3だけBB/RB付きで取得：約6分で complete、310台全件・ジャグラー102台。本番DBとの比較で、基礎列の変更0件、10/3以外のBB/RB変更0件、既存のBB/RB値の変更0件。取得終了後に取得用ブラウザーのウィンドウが閉じたことを確認。
- 本番の SQLite・CSV は変更していない（読み取り専用で参照のみ）。

### 未完了・注意

- 全日程のBB/RB補完は未実行。BB/RB不足は1,025日・317,685行。1日約6〜8分のため、全期間で連続約100時間かかる見込み（1ページ3秒の間隔は負荷抑制のため維持）。途中で停止しても、揃った機種は再取得しない。
- 通常の「本日までのデータをスクレイプ」は前方更新のまま。過去日のBB/RBは「指定範囲を取得」（BB・RBにチェック）または基礎データシートの操作で補完する。
- 修正はまだ配布パッケージになっていない。手元の 1.3.1（`Downloads\JokersEye-1.3.1-windows (2)`）は旧コードのまま。
- `application.log` は `/api/scrape/browser-task` の0.75秒ごとのポーリングを毎回記録しており、11MB超に増えている（未対応）。

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
- （2026-10-06 ユーザー報告）Windowsでダウンロードした ZIP を「すべて展開」した版が起動しなかった。展開した全ファイルに「インターネットから取得」の印（Zone.Identifier）が付き、.NET Framework が `Python.Runtime.dll` の読み込みを拒否したため（`Failed to resolve Python.Runtime.Loader.Initialize`）。EXE の横に `Joker's eye.exe.config`（`loadFromRemoteSources`）を置いて解決。CI の起動確認は、この印を付けたコピーで行う。同時に、日本語Windowsでエラー表示自体が cp932 の文字コードで失敗していた問題も修正。

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
