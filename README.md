# Timeback

生活リズムや予定をもとに、次にすることを考えるStreamlitアプリです。

## ローカルで実行

```bash
python -m pip install -r requirements.txt
streamlit run Timeback/timeback.py
```

## Streamlit Community Cloudへの公開

このリポジトリをStreamlit Community Cloudに接続し、`Timeback/timeback.py` をアプリのエントリーポイントに指定してください。依存パッケージはルートの `requirements.txt` からインストールされます。

データはブラウザーのStreamlitセッション内だけに保持され、サーバー上のファイルや共有データベースには保存されません。セッションが切れたり、アプリが再起動したりすると消えます。利用者同士でデータは共有されません。

時間割画像の読み取りを有効にするには、StreamlitのSecretsに `ANTHROPIC_API_KEY` を設定します。画像はAnthropicに送信されます。公開アプリで設定すると、利用者がAPI使用量を消費できるため、利用制限なしにキーを設定しないでください。
