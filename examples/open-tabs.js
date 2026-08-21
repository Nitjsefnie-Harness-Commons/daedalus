// Open background tabs for preview
// Env vars: __URLS__ = comma-separated URLs
const urls = '__URLS__'.split(',');
urls.forEach(url => GM.openInTab(url.trim(), {active: false}));
return 'opened ' + urls.length + ' tabs';
