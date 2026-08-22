import {
  defineRailway,
  github,
  postgres,
  project,
  service,
} from "railway/iac";

export default defineRailway((ctx) => {
  const database = postgres("GABAY PostgreSQL");
  const web = service("GABAY Web", {
    source: github("Gabbu69/GABAY", { branch: "main" }),
    build: "pip install -r requirements.txt && python manage.py collectstatic --noinput",
    start:
      "python manage.py migrate --noinput && " +
      "gunicorn gabay.wsgi:application --bind 0.0.0.0:$PORT --workers 2 --threads 2 --timeout 120",
    healthcheck: "/health/",
    healthcheckTimeout: 120,
    env: {
      GABAY_PROFILE: "production",
      GABAY_DATABASE_URL: database.env.DATABASE_URL,
      GABAY_SECRET_KEY: ctx.shared.GABAY_SECRET_KEY,
      PYTHONUNBUFFERED: "1",
    },
  });

  return project("GABAY", { resources: [database, web] });
});
