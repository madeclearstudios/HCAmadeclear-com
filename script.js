// HCA Made Clear — placeholder front-end logic.
// This is a stand-in for the real search: once Supabase is wired up,
// replace performSearch() with a query against the scenes table
// (full-text search or embeddings), and replace loadFilms() with a
// fetch from Supabase instead of the local JSON file.

let filmsData = [];

async function loadFilms() {
  const res = await fetch('data/films.json');
  const json = await res.json();
  filmsData = json.films;
  renderFilmGrid();
}

function renderFilmGrid() {
  const grid = document.getElementById('film-grid');
  const count = document.getElementById('film-count');
  const indexedCount = filmsData.filter(f => f.status === 'indexed').length;
  count.textContent = `${indexedCount} of ${filmsData.length} films indexed`;

  grid.innerHTML = filmsData.map(film => {
    const isIndexed = film.status === 'indexed';
    return `
      <div class="film-card ${isIndexed ? 'indexed' : ''}">
        <div class="film-thumb">${isIndexed ? '' : 'awaiting tagging'}</div>
        <div class="film-info">
          <h3>${film.title}</h3>
          <div class="film-status ${isIndexed ? 'indexed' : ''}">
            <span class="status-dot"></span>
            ${isIndexed ? `${film.sceneCount} scenes tagged` : 'not yet indexed'}
          </div>
        </div>
      </div>
    `;
  }).join('');
}

function performSearch(query) {
  const q = query.trim().toLowerCase();
  if (!q) return [];

  const matches = [];
  filmsData.forEach(film => {
    (film.scenes || []).forEach(scene => {
      const haystack = [scene.title, scene.description, ...(scene.tags || [])]
        .join(' ')
        .toLowerCase();
      if (haystack.includes(q)) {
        matches.push({ film: film.title, ...scene });
      }
    });
  });
  return matches;
}

function renderResults(matches) {
  const section = document.getElementById('results');
  const grid = document.getElementById('results-grid');

  if (matches.length === 0) {
    section.hidden = false;
    grid.innerHTML = '<p>No tagged scenes match that yet — only The Wellington has been indexed so far.</p>';
    return;
  }

  section.hidden = false;
  grid.innerHTML = matches.map(m => `
    <div class="result-card">
      <span class="timecode">${m.film} — ${m.start}–${m.end}</span>
      <h4>${m.title}</h4>
      <p>${m.description}</p>
      <div class="tags">${(m.tags || []).join(', ')}</div>
    </div>
  `).join('');
}

function init() {
  loadFilms();

  const form = document.getElementById('search-form');
  const input = document.getElementById('search-input');

  form.addEventListener('submit', e => {
    e.preventDefault();
    renderResults(performSearch(input.value));
  });

  document.querySelectorAll('.pill').forEach(pill => {
    pill.addEventListener('click', () => {
      input.value = pill.textContent;
      renderResults(performSearch(input.value));
    });
  });
}

document.addEventListener('DOMContentLoaded', init);
