# WordPress Plugin Flow

Use this reference when preparing reports for the Analysis Reports WordPress plugin.

## Upload Metadata

Confirm before upload:

- analysis key, such as `husna`, `lotta`, `veera`, or another stable key
- version, usually `YYYY-MM-DD`
- language, usually `fi` or `en` if the plugin requires it
- whether this version should become `latest`
- bundle ZIP path

## Route Expectations

The plugin serves routes such as:

- `/reports/<analysis_key>/<version>/`
- `/reports/<analysis_key>/latest/`

Assets should load through the same protected route.

## Safety Expectations

The plugin should reject unsafe bundles, but validate locally first:

- root `index.html` exists
- no server-executable files are present
- no path traversal exists in ZIP entries
- previous explicit version URLs remain valid after publishing a new latest version
