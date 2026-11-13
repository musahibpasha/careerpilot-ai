from app.glassdoor_search import build_glassdoor_url


def test_glassdoor_url_defaults_to_global_site_not_india():
    assert build_glassdoor_url().startswith("https://www.glassdoor.com/")
    assert build_glassdoor_url(use_india_domain=True).startswith(
        "https://www.glassdoor.co.in/"
    )
