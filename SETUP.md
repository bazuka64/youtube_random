# セットアップ手順

## 1. 依存パッケージのインストール

```
pip install -r requirements.txt
```

## 2. Google API の認証情報を取得

1. [Google Cloud Console](https://console.cloud.google.com/) にアクセス
2. 新しいプロジェクトを作成（または既存のプロジェクトを選択）
3. 左メニュー → **APIとサービス** → **ライブラリ**
4. `YouTube Data API v3` を検索して **有効にする**
5. 左メニュー → **APIとサービス** → **認証情報**
6. **認証情報を作成** → **OAuthクライアントID**
7. アプリケーションの種類: **デスクトップアプリ**
8. 作成後、**JSONをダウンロード**
9. ダウンロードしたファイルを `client_secrets.json` という名前でこのフォルダに置く

> 初回のみ「OAuthの同意画面」の設定が必要です。
> - ユーザーの種類: **外部**
> - アプリ名: 任意（例: YouTube Random）
> - テストユーザーに自分のGoogleアカウントを追加

## 3. 実行

```
python main.py
```

初回はブラウザが開いてGoogleアカウントでのログインを求められます。
認証後は `token.pickle` に保存されるため、2回目以降は不要です。
