(() => {
  const form = document.querySelector('form[action$="/vouchers"]');
  if (!form) return;

  const submit = form.querySelector('button[type="submit"]');
  const label = document.createElement('label');
  label.htmlFor = 'voucher-speed-tier';
  label.append(document.createTextNode('Speed tier'));

  const help = document.createElement('span');
  help.className = 'field-help';
  help.textContent = 'Download / upload';

  const select = document.createElement('select');
  select.id = 'voucher-speed-tier';
  select.name = 'speed_tier';
  select.required = true;
  select.disabled = true;
  label.append(help, select);
  form.insertBefore(label, submit);

  submit.disabled = true;
  fetch('/api/admin/voucher-speed-tiers', { headers: { Accept: 'application/json' } })
    .then((response) => {
      if (!response.ok) throw new Error('Could not load voucher speed tiers.');
      return response.json();
    })
    .then(({ tiers }) => {
      if (!Array.isArray(tiers) || tiers.length === 0) {
        throw new Error('No voucher speed tiers are configured.');
      }
      tiers.forEach((tier) => {
        const option = document.createElement('option');
        option.value = tier.key;
        option.textContent = tier.label;
        option.selected = tier.key === '2/1';
        select.append(option);
      });
      select.disabled = false;
      submit.disabled = false;
    })
    .catch((error) => {
      const message = document.createElement('small');
      message.className = 'voucher-tier-error';
      message.setAttribute('role', 'alert');
      message.textContent = error.message;
      label.append(message);
    });

  fetch('/api/admin/voucher-inventory-tiers', { headers: { Accept: 'application/json' } })
    .then((response) => {
      if (!response.ok) throw new Error('Could not load voucher tier labels.');
      return response.json();
    })
    .then(({ vouchers }) => {
      const rows = new Map([...document.querySelectorAll('#voucher-rows tr[data-status]')]
        .map((row) => [row.querySelector('code')?.textContent, row]));
      vouchers.forEach((voucher) => {
        const creditCell = rows.get(voucher.code)?.children[1];
        if (!creditCell) return;
        const tier = document.createElement('small');
        tier.className = 'voucher-tier-info';
        tier.textContent = voucher.label;
        creditCell.append(tier);
      });
    })
    .catch(() => {});
})();
