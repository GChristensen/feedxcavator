// Runs in the page (load_js("posts")). Marks each post with data-fx so Python can take its screenshot,
// and returns the post's selector, link, text and time.

function main(limit) {
  const posts = [...document.querySelectorAll('article[data-testid=tweet]')].slice(0, limit)
  return posts.map((post, i) => {
    post.dataset.fx = i
    return {
      selector: `article[data-fx="${i}"]`,
      link: post.querySelector('a[href*="/status/"]')?.href,
      text: post.querySelector('[data-testid=tweetText]')?.innerText || '',
      published: post.querySelector('time')?.getAttribute('datetime'),
    }
  }).filter((post) => post.link)
}