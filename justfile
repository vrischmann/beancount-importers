default:
    @just --list

# Run the test suite
test *args:
    uv run pytest {{ args }}
