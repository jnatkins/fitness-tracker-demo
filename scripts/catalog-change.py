"""For cloud, run `op run --env-file=.env -- uv run python scripts/catalog-change.py` while the app is running.
Creates/updates a synthetic food in Postgres; the embedding worker enriches it and ClickPipes replicates it in cloud mode.
"""
from health_demo.db import pg
with pg() as p:
    p.execute('''INSERT INTO foods(id,name,description,calories,protein,carbs,fat,serving_grams,vegan) VALUES
      ('skyr-bowl','Skyr berry bowl','Icelandic cultured yogurt with berries; tangy high protein breakfast',90,12,8,1,250,false)
      ON CONFLICT(id) DO UPDATE SET description=EXCLUDED.description''')
print('Postgres change committed. The embedding worker and ClickPipe will deliver it shortly; then search for skyr.')
