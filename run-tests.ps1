# Runs the test suite inside the oTube Docker image (it has ffmpeg).
# The tests make their own short clips in a temporary folder; your library isn't touched.
docker compose build otube
docker run --rm -v "${PSScriptRoot}:/src" -w /src --entrypoint sh oyoutube-otube -c "pip install -q pytest && python -m pytest -q -p no:cacheprovider tests"
exit $LASTEXITCODE
