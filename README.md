# Game Maintenance Calendar

Google Calendar向けのICSフィードを自動生成します。

## 対応ゲーム

- NTE
- アークナイツ：エンドフィールド
- 原神
- 崩壊：スターレイル
- ステラソラ
- ドルフィンウェーブ
- ブルーアーカイブ
- 勝利の女神：NIKKE
- 鳴潮

公式日本語ニュースページからメンテナンス関連の記事を探し、日時を抽出します。
エンドフィールドだけはAsia/UTC+8の時間を使用し、日本時間へ変換します。

## GitHubでの設定

1. Public repositoryを作成
2. このフォルダの中身をリポジトリのルートへアップロード
3. Settings > Pages > Build and deployment > Source を GitHub Actions にする
4. Actions > Update game maintenance calendar > Run workflow を一度実行
5. Pagesに表示されたURLの `calendar.ics` をGoogleカレンダーの「URLで追加」に登録

## 注意

各公式サイトのHTML構造や告知文面が変更された場合、抽出ロジックの調整が必要になることがあります。
また、Googleカレンダー側の外部カレンダー更新にはタイムラグがあります。
