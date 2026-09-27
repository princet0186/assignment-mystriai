const currency = new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR' });
const money = n => currency.format(n);
const text = (tag, value, className = '') => {
  const node = document.createElement(tag);
  node.textContent = value;
  node.className = className;
  return node;
};

async function refresh() {
  const status = document.querySelector('#status').value;
  const responses = await Promise.all([fetch('/api/overview'), fetch(`/api/invoices?status=${encodeURIComponent(status)}`)]);
  const failed = responses.find(r => !r.ok);
  if (failed) {
    const detail = await failed.json().then(d => d.error).catch(() => null);
    throw new Error(detail || `Could not refresh the register (${failed.status}).`);
  }
  const [data, rows] = await Promise.all(responses.map(r => r.json()));
  document.querySelector('#invoice-count').textContent = data.summary.invoice_count;
  document.querySelector('#open-count').textContent = data.summary.open_count;
  document.querySelector('#outstanding').textContent = money(data.summary.outstanding);
  const body = document.querySelector('#invoices');
  body.replaceChildren();
  rows.forEach(r => {
    const row = document.createElement('tr');
    [r.customer_name, r.invoice_number, r.due_date].forEach(v => row.append(text('td', v)));
    [r.amount, r.paid, r.balance].forEach(v => row.append(text('td', money(v), 'number')));
    row.append(text('td', r.status));
    body.append(row);
  });
  const unmatched = document.querySelector('#unmatched');
  unmatched.replaceChildren(...data.unmatched_payments.map(p => text('li', `${p.payment_id} · ${p.customer_id} / ${p.invoice_number} · ${money(p.amount)}`)));
  if (!data.unmatched_payments.length) unmatched.append(text('li', 'No unmatched payments.'));
  document.querySelector('#page-error').textContent = '';
}

const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;

// Describe what the server actually did, so the message can never claim a
// success the import did not have.
const summarise = ({ imported = 0, skipped = 0, rejected = 0 }) => {
  const parts = [`${plural(imported, 'row')} imported`];
  if (skipped) parts.push(`${skipped} skipped as already present`);
  if (rejected) parts.push(`${plural(rejected, 'row')} rejected`);
  return parts.join(', ') + '.';
};

async function submitImport(form, dryRun = false) {
  const feedback = form.querySelector('.feedback');
  const buttons = form.querySelectorAll('button');
  buttons.forEach(b => { b.disabled = true; });
  feedback.className = 'feedback';
  feedback.textContent = dryRun ? 'Checking…' : 'Importing…';
  try {
    const file = form.querySelector('input').files[0];
    if (!file) throw new Error('Choose a CSV file first.');
    const query = `kind=${form.dataset.kind}${dryRun ? '&dry_run=1' : ''}`;
    const response = await fetch(`/api/import?${query}`, {
      method: 'POST', headers: { 'Content-Type': 'text/csv' }, body: await file.text()
    });
    const payload = await response.json().catch(() => null);
    if (!response.ok) {
      throw new Error(payload?.error || `The server returned ${response.status}.`);
    }
    feedback.className = payload.rejected ? 'feedback warn' : 'feedback ok';
    const lead = payload.dry_run
      ? `Preview only, nothing saved: ${summarise(payload)}`
      : summarise(payload);
    feedback.replaceChildren(text('span', lead));
    // Show every rejected line so the owner can correct the file and retry.
    if (payload.errors?.length) {
      const list = document.createElement('ul');
      list.className = 'errors';
      payload.errors.forEach(e => list.append(text('li', `Line ${e.line}: ${e.reason}`)));
      feedback.append(list);
    }
    if (!payload.dry_run) await refresh();
  } catch (error) {
    feedback.className = 'feedback bad';
    feedback.textContent = `${dryRun ? 'Check' : 'Import'} failed: ${error.message}`;
  } finally {
    buttons.forEach(b => { b.disabled = false; });
  }
}

document.querySelector('#status').addEventListener('change', () => refresh().catch(e => { document.querySelector('#page-error').textContent = e.message; }));
document.querySelectorAll('form[data-kind]').forEach(form => {
  form.addEventListener('submit', e => { e.preventDefault(); submitImport(form); });
  // Preview an import before committing it, so a retry cannot surprise the owner.
  form.querySelector('[data-check]').addEventListener('click', () => submitImport(form, true));

  // Handle file input changes for dropzone
  const fileInput = form.querySelector('input[type="file"]');
  const dropzone = form.querySelector('.dropzone');
  const fileNameDisplay = form.querySelector('.file-name');
  const textDisplay = form.querySelector('.text');

  if (fileInput) {
    fileInput.addEventListener('change', () => {
      if (fileInput.files.length > 0) {
        fileNameDisplay.textContent = `Selected: ${fileInput.files[0].name}`;
        textDisplay.style.display = 'none';
      } else {
        fileNameDisplay.textContent = '';
        textDisplay.style.display = 'block';
      }
    });

    ['dragenter', 'dragover'].forEach(eventName => {
      fileInput.addEventListener(eventName, () => dropzone.classList.add('dragover'));
    });
    ['dragleave', 'drop'].forEach(eventName => {
      fileInput.addEventListener(eventName, () => dropzone.classList.remove('dragover'));
    });
  }
});
refresh().catch(e => { document.querySelector('#page-error').textContent = e.message; });
