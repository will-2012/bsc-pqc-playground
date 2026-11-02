import {fileURLToPath} from 'node:url';
process.title = 'bsc-pqc-bundler';
const config = process.argv[2];
const cli = new URL('../.runtime/build/alto/src/esm/cli/alto.js', import.meta.url);
process.argv = [process.execPath, fileURLToPath(cli), 'run', '--config', config];
await import(cli.href);
