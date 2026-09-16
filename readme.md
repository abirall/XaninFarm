Recommended: Docker
1. Install Docker Desktop for Windows, then restart.

2. Open a terminal in the project folder and run:


docker compose build
3. Create the database migration files (these don't exist yet):


docker compose run --rm web python manage.py makemigrations
4. Start everything:


docker compose up
5. In a second terminal, make your admin login:


docker compose exec web python manage.py createsuperuser
Then open:

Shop → http://localhost:8000
Staff dashboard → http://localhost:8000/dashboard/
Emails → http://localhost:8025
No .env needed for local — the compose file has dev defaults built in.

Alternative: Python directly
Only if you'd rather not use Docker. You'd need Python 3.12+, PostgreSQL 16 and Redis installed and running yourself, then:


python -m venv .venv
.venv\Scripts\activate
pip install -r requirements/dev.txt
copy .env.example .env
python manage.py makemigrations
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver --settings=config.settings.dev
You'd also need Node to build the CSS (npm install && npm run build), or set TAILWIND_CDN_FALLBACK=True in .env to skip it.

One thing to expect. Step 3 is the first time any of this code will actually execute. I'd be surprised if makemigrations passed cleanly on the first try — that's the normal outcome for a build this size that's never been run, and it's exactly the step we agreed would surface the errors.

So: run step 3, paste me whatever it prints, and I'll fix it. Don't worry about the errors looking bad — that's the point of the step.