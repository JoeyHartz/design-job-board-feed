// Create, update or close the "Jobs waiting for approval" issue from reports/approvals.md.
// Used by the Build feed, Approvals and Daily maintenance workflows (actions/github-script).
// When new jobs appear, it comments and mentions the approvers so they get a notification.
module.exports = async ({ github, context }) => {
  const fs = require('fs');
  const cfg = JSON.parse(fs.readFileSync('config/board.json', 'utf8')).approval || {};
  if (!cfg.required) return;
  const label = cfg.issueLabel || 'pending-approval';
  const approvers = (cfg.approvers || []).map(a => '@' + a).join(' ');
  const body = fs.readFileSync('reports/approvals.md', 'utf8');
  const ids = b => new Set([...(b || '').matchAll(/^\s*[-*]\s+\[[ xX]\]\s+Approve\b[^`\n]*`([^`]+)`/gm)].map(m => m[1]));
  const now = ids(body);
  const title = `Jobs waiting for approval (${now.size})`;

  try { await github.rest.issues.getLabel({ ...context.repo, name: label }); }
  catch (e) { await github.rest.issues.createLabel({ ...context.repo, name: label, color: 'fbca04' }); }
  const open = (await github.rest.issues.listForRepo({ ...context.repo, state: 'open', labels: label })).data;
  const issue = open[0];

  if (now.size === 0) {
    if (issue) {
      await github.rest.issues.update({ ...context.repo, issue_number: issue.number, title, body,
                                        state: 'closed', state_reason: 'completed' });
    }
    return;
  }
  if (issue) {
    const before = ids(issue.body);
    const added = [...now].filter(id => !before.has(id));
    if (issue.body !== body || issue.title !== title) {
      await github.rest.issues.update({ ...context.repo, issue_number: issue.number, title, body });
    }
    if (added.length) {
      await github.rest.issues.createComment({ ...context.repo, issue_number: issue.number,
        body: `${approvers} ${added.length} new job${added.length > 1 ? 's' : ''} to review: ${added.map(a => '`' + a + '`').join(', ')}` });
    }
  } else {
    const created = await github.rest.issues.create({ ...context.repo, title, labels: [label], body });
    await github.rest.issues.createComment({ ...context.repo, issue_number: created.data.number,
      body: `${approvers} ${now.size} job${now.size > 1 ? 's are' : ' is'} waiting for your approval.` });
  }
};
