Shop Tracker backend – files to upload to GitHub for Render.

Render settings
  Build command:  pip install -r requirements.txt
  Start command:  uvicorn app.main:app --host 0.0.0.0 --port $PORT

Environment variables (copy the values from backend/.env on your Mac – never upload the .env file itself)
  DATABASE_URL           same as in .env
  SECRET_KEY             same as in .env
  ENV                    prod
  FRONTEND_ORIGINS       your Netlify address, e.g. https://sri-sakthivel.netlify.app
  FORCE_PASSWORD_CHANGE  true

Check it works: open https://<your-render-address>/health  → {"ok":true}
