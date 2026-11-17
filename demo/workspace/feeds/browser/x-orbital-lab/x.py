# The simplest browser extractor: open the page in Camoufox, wait for the posts, scroll a little to load
# more, and read them with the selectors of feed.yaml.

@extractor("x-profile")
def x_profile(feed):
    profile = (feed.params or {}).get("profile", "default")
    with browser(profile=profile) as b:
        page = b.open(feed.source, wait_for="article[data-testid=tweet]", scroll=2, timeout=60)
        return page.apply_selectors(feed)
