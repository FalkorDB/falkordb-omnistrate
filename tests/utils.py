import requests


def get_last_gh_tag():
    """Get the last tag from GitHub."""
    response = requests.get(
        "https://api.github.com/repos/FalkorDB/falkordb-omnistrate/tags",
        timeout=60,
    )

    response.raise_for_status()

    return response.json()[0]["name"]
