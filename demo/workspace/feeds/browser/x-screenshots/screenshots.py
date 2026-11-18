# Posts as pictures: posts.js finds the posts on the page, then each one is screenshotted and kept in the
# image store (/image/<id>), and the headline shows the picture.

from html import escape

# hidden while a shot is taken: the sticky bars that would lie over the post
HIDE = "[data-testid=TopNavBar], [data-testid=BottomBar] {visibility: hidden !important}"


@extractor("x-screenshots")
def x_screenshots(feed):
    with browser(profile=feed.params["profile"]) as b:
        page = b.open(feed.source, wait_for="article[data-testid=tweet]", timeout=60)
        headlines = []
        for post in page.evaluate(load_js("posts"), 10):
            shot = page.screenshot(post["selector"], type="jpeg", quality=85, style=HIDE)
            image = store_image(shot, "image/jpeg", post["link"])
            headlines.append({
                "title": post["text"][:100] or "(a post without text)",
                "link": post["link"],
                "published": post["published"],
                "image": image,
                "summary": f'<p>{escape(post["text"])}</p><img src="{image}">',
            })
        return headlines