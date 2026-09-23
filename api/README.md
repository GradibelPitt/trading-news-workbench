# API Configuration

This folder is for local API configuration and integration notes.

## Files

- `.env.local`: local secrets. Do not commit or share this file.
- `.env.example`: template for required variables.
- `README.md`: setup and usage notes.

## DeepSeek

The app should read the key from environment variables, not from `index.html` or client-side JavaScript.

Expected variables:

```powershell
DEEPSEEK_API_KEY=your_key_here
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat
```

## CLI / server usage

Future AI analysis code should load these values on the server side only. The browser should call local endpoints such as `/api/analyze`, and the server should call DeepSeek.
