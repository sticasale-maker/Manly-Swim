// One-page build (sea.html, 7 Oct 2026): the hub and every chapter are on one page, so
// every chapter link is an anchor on it (#<slug>; the page's fold script opens it) and
// the hub view also gets the read buttons and the hide switch for each section (single).
window.OCEAN_PROGRESS = {
  single: true,
  hub: '#contents',
  hrefs: {
    'waves': '#waves', 'swell-and-sets': '#swell-and-sets', 'into-the-bay': '#into-the-bay',
    'point-and-wall': '#point-and-wall', 'wind': '#wind', 'currents': '#currents', 'circulation': '#circulation', 'simulator': '#simulator',
    'sand': '#sand', 'tides': '#tides', 'sea-level': '#sea-level', 'cold-water': '#cold-water', 'words': '#words'
  }
};
