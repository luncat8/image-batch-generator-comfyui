// Export the authoritative genre inventory from a local Music Tree checkout.
const {resolve} = require('node:path');
const checkout = process.argv[2];
if (!checkout) {
	console.error('Usage: node tools/music-tree-genres.cjs /path/to/music-tree');
	process.exit(1);
}
const catalog = require(resolve(checkout, 'js/catalog.js'));
process.stdout.write(JSON.stringify(catalog.genres.map(({id, name, family}) => (
	{genre_id: id, name, family}
)), null, '\t') + '\n');
