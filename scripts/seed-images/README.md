# Seed image fixtures

These JPEGs are small public sample images used by `scripts/seed-local.py` for the LocalStack demo. The seeder rotates through them when creating records, so the S3 objects contain real image bytes rather than a repeated placeholder.

Sources:

- `portrait.jpg`: [Unsplash image CDN](https://images.unsplash.com/photo-1503023345310-bd7c1de61c7d?w=640&q=80)
- `mountains.jpg`: [Unsplash image CDN](https://images.unsplash.com/photo-1519681393784-d120267933ba?w=640&q=80)
- `landscape.jpg`: [Unsplash image CDN](https://images.unsplash.com/photo-1500534623283-312aade485b7?w=640&q=80)

The fixtures are included only for local demonstration and test data. They are not application assets served by the API.
