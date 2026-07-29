// Extract a LinkedIn profile's original posts from the browser, for corpus widening.
//
// Zernio only knows posts published THROUGH Zernio, so a profile's earlier history is
// invisible to it. This runs in an authenticated tab on
//   https://www.linkedin.com/in/<slug>/recent-activity/shares/
// (which normalizes to /recent-activity/all/ with the "Posts" filter applied) and exports
// JSON for POST /corpus/linkedin.
//
// Facts this encodes, each learned the hard way:
//
//  * The DOM carries `urn:li:activity:<id>`; the corpus stores `urn:li:share:<id>`. They are
//    DIFFERENT identifiers for the same post — joining on them matches nothing. The backend
//    joins on normalized content instead.
//  * An activity id encodes its creation time in the upper bits (`id >> 22` = epoch ms), so
//    exact publish dates come free rather than from parsing "11h ago".
//  * Cards are virtualized: scroll past one and it unmounts. Sampling after each scroll
//    silently loses posts, so a MutationObserver plus a poll harvests as cards appear.
//  * `…more` is a trailing UI label, not evidence of truncation — `innerText` already holds
//    the whole post. It is stripped rather than clicked around.
//  * The pagination button reads exactly "Show more", not "Show more results".
//
// ponytail: LinkedIn rate-limits sustained scrolling — a session degrades from ~87 posts to
// a handful. Runs are therefore partial by design; re-run across sessions and let the
// backend's content-match upsert merge them. Automating the retry cadence would only get
// the account flagged faster.

const CUTOFF = '2021-07-29'; // 5-year window
const PROFILE_NAME = 'Monte Desai';

window.__scraped = window.__scraped || new Map();

window.__expand = () =>
  [...document.querySelectorAll('button,span[role="button"]')]
    .filter((b) => /…more|see more/i.test(b.innerText || ''))
    .forEach((b) => { try { b.click(); } catch { /* card may unmount mid-click */ } });

window.__harvest = function () {
  document.querySelectorAll('[data-urn^="urn:li:activity"],[data-id^="urn:li:activity"]').forEach((card) => {
    const urn = card.getAttribute('data-urn') || card.getAttribute('data-id');
    const published_at = new Date(Number(BigInt(urn.split(':').pop()) >> 22n)).toISOString();
    if (published_at < CUTOFF) return;

    const el = card.querySelector('.update-components-text, .feed-shared-inline-show-more-text');
    const content = el ? el.innerText.replace(/\s*…more\s*$/, '').trim() : '';
    if (!content) return; // not hydrated yet; the poll retries

    const prev = window.__scraped.get(urn);
    if (prev && prev.content.length >= content.length) return; // keep the fuller capture

    const counts = card.querySelector('.social-details-social-counts');
    const text = counts ? counts.innerText : '';
    const num = (re) => { const m = text.match(re); return m ? parseInt(m[1].replace(/,/g, '')) : 0; };
    const reactions = counts && counts.querySelector('.social-details-social-counts__reactions-count');

    const images = [...card.querySelectorAll('.update-components-image img, .update-components-article img')]
      .map((i) => i.currentSrc || i.src)
      .filter((s) => s && s.includes('licdn') && !s.includes('static.licdn'));
    const isVideo = !!card.querySelector('.update-components-linkedin-video, video');

    window.__scraped.set(urn, {
      urn, published_at, content,
      likes: reactions ? parseInt(reactions.innerText.replace(/[^0-9]/g, '') || '0') : num(/^\s*([\d,]+)/),
      comments: num(/([\d,]+)\s+comment/i),
      shares: num(/([\d,]+)\s+repost/i), // Zernio reports 0 shares on every row; this is the only source
      media_urls: images,
      media_type: isVideo ? 'video' : (images.length ? 'image' : null), // video is never downloaded
      url: 'https://www.linkedin.com/feed/update/' + urn + '/',
    });
  });
  return window.__scraped.size;
};

window.__watch = function () {
  if (window.__obs) window.__obs.disconnect();
  if (window.__poll) clearInterval(window.__poll);
  window.__obs = new MutationObserver(() => { window.__expand(); window.__harvest(); });
  window.__obs.observe(document.body, { childList: true, subtree: true });
  window.__poll = setInterval(() => { window.__expand(); window.__harvest(); }, 300);
};

// Keep each call under the 45s CDP evaluate ceiling — roughly 12 steps.
window.__step = async function (n) {
  for (let i = 0; i < n; i++) {
    const more = [...document.querySelectorAll('button')].find((b) => /^show more$/i.test(b.innerText.trim()));
    if (more) more.click();
    window.scrollTo(0, document.body.scrollHeight);
    await new Promise((r) => setTimeout(r, 1500));
  }
  return window.__scraped.size;
};

window.__stop = () => { window.__obs?.disconnect(); clearInterval(window.__poll); };

// Chrome blocks a second automatic download per page load — reload before re-exporting.
window.__export = function () {
  window.__stop();
  const posts = [...window.__scraped.values()].sort((a, b) => b.published_at.localeCompare(a.published_at));
  const blob = new Blob([JSON.stringify({
    account: PROFILE_NAME, profile: location.href.split('/recent-activity')[0],
    captured_at: new Date().toISOString(), count: posts.length, posts,
  }, null, 1)], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'monte-posts-full.json';
  document.body.appendChild(a); a.click(); a.remove();
  return { exported: posts.length };
};

// Usage: __watch(); then await __step(12) until the count stops climbing; then __export().
