// Run inside the n8n container: prints the number of keys stored by the
// Remove Duplicates node (workflow scope) for the builtin workflow.
const path = require('path');
const glob = require('fs').readdirSync('/usr/local/lib/node_modules/n8n/node_modules/.pnpm').find((d) => d.startsWith('sqlite3@'));
const sqlite3 = require(path.join('/usr/local/lib/node_modules/n8n/node_modules/.pnpm', glob, 'node_modules/sqlite3'));
const db = new sqlite3.Database('/home/node/.n8n/database.sqlite', sqlite3.OPEN_READONLY);
db.all("SELECT workflowId, context, value FROM processed_data WHERE workflowId = 'dedupeBuiltin002'", (err, rows) => {
  if (err) { console.log('ERR', err.message); return; }
  for (const r of rows) {
    const v = JSON.parse(r.value);
    console.log(JSON.stringify({ workflowId: r.workflowId, context: r.context, mode: v.mode, entries: v.data.length }));
  }
  if (!rows.length) console.log('no rows');
});
