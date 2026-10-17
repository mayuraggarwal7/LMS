web: python manage.py migrate --noinput && python manage.py seed_demo && gunicorn campusflow.wsgi --bind 0.0.0.0:$PORT --workers 2 --timeout 120
