(() => {
  const panel = document.getElementById('speed-tiers');
  if (!panel) return;

  const api = panel.dataset.tierApi;
  const rows = panel.querySelector('#speed-tier-rows');
  const download = panel.querySelector('#tier-download');
  const upload = panel.querySelector('#tier-upload');
  const profile = panel.querySelector('#tier-profile');
  const save = panel.querySelector('#tier-save');
  const cancel = panel.querySelector('#tier-cancel');
  const message = panel.querySelector('#tier-message');
  let editingId = null;

  const setMessage = (text, isError = false) => {
    message.textContent = text;
    message.dataset.error = String(isError);
  };

  const resetEditor = () => {
    editingId = null;
    download.value = '';
    upload.value = '';
    profile.value = '';
    save.textContent = 'Add tier';
    cancel.hidden = true;
  };

  const request = async (url, options = {}) => {
    const response = await fetch(url, {
      ...options,
      headers: { Accept: 'application/json', 'Content-Type': 'application/json', ...options.headers },
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Speed tier request failed.');
    return result;
  };

  const loadTiers = async () => {
    rows.replaceChildren();
    try {
      const { tiers } = await request(api);
      if (!tiers.length) {
        const row = document.createElement('tr');
        const cell = document.createElement('td');
        cell.colSpan = 3;
        cell.className = 'empty-state';
        cell.textContent = 'No active tiers. Add one above.';
        row.append(cell);
        rows.append(row);
        return;
      }
      tiers.forEach((tier) => {
        const row = document.createElement('tr');
        const rate = document.createElement('td');
        rate.textContent = `${tier.download} / ${tier.upload} Mbps`;
        const routerProfile = document.createElement('td');
        routerProfile.textContent = tier.profile;
        const actions = document.createElement('td');
        actions.className = 'tier-actions';

        const edit = document.createElement('button');
        edit.className = 'icon-action';
        edit.type = 'button';
        edit.textContent = 'Edit';
        edit.addEventListener('click', () => {
          editingId = tier.id;
          download.value = tier.download;
          upload.value = tier.upload;
          profile.value = tier.profile;
          save.textContent = 'Save changes';
          cancel.hidden = false;
          download.focus();
        });

        const archive = document.createElement('button');
        archive.className = 'icon-action danger';
        archive.type = 'button';
        archive.textContent = 'Archive';
        archive.addEventListener('click', async () => {
          if (!window.confirm(`Archive ${tier.profile}? Existing vouchers keep their assigned profile.`)) return;
          try {
            await request(`${api}/${tier.id}`, { method: 'DELETE' });
            if (editingId === tier.id) resetEditor();
            await loadTiers();
            window.location.reload();
          } catch (error) {
            setMessage(error.message, true);
          }
        });

        actions.append(edit, archive);
        row.append(rate, routerProfile, actions);
        rows.append(row);
      });
    } catch (error) {
      setMessage(error.message, true);
      const row = document.createElement('tr');
      const cell = document.createElement('td');
      cell.colSpan = 3;
      cell.className = 'empty-state';
      cell.textContent = error.message;
      row.append(cell);
      rows.append(row);
    }
  };

  save.addEventListener('click', async () => {
    const values = {
      download_limit_mbps: download.value,
      upload_limit_mbps: upload.value,
      router_profile: profile.value,
    };
    try {
      await request(editingId ? `${api}/${editingId}` : api, {
        method: editingId ? 'PUT' : 'POST',
        body: JSON.stringify(values),
      });
      window.location.reload();
    } catch (error) {
      setMessage(error.message, true);
    }
  });

  cancel.addEventListener('click', resetEditor);
  loadTiers();
})();
