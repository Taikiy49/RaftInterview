const { spawn } = require('child_process');
const net = require('net');
const path = require('path');

const rootDir = path.resolve(__dirname, '..', '..');
const appDir = path.resolve(__dirname, '..');
const isWindows = process.platform === 'win32';
const children = [];

function run(name, command, args, cwd, env = {}) {
  const child = spawn(command, args, {
    cwd,
    env: { ...process.env, ...env },
    shell: isWindows,
    stdio: 'inherit',
  });

  children.push(child);
  child.on('exit', (code, signal) => {
    if (!signal && code && code !== 0) {
      console.error(`${name} exited with code ${code}`);
      shutdown(code);
    }
  });
}

function isPortOpen(port) {
  return new Promise((resolve) => {
    const socket = net.createConnection({ host: '127.0.0.1', port });
    socket.on('connect', () => {
      socket.destroy();
      resolve(true);
    });
    socket.on('error', () => resolve(false));
    socket.setTimeout(500, () => {
      socket.destroy();
      resolve(false);
    });
  });
}

function shutdown(code = 0) {
  for (const child of children) {
    if (!child.killed) child.kill();
  }
  process.exit(code);
}

process.on('SIGINT', () => shutdown(0));
process.on('SIGTERM', () => shutdown(0));

(async () => {
  if (await isPortOpen(8000)) {
    console.log('agent-api already listening on http://127.0.0.1:8000');
  } else {
    run('agent-api', 'python', ['main.py', '--web', '--port', '8000'], rootDir);
  }

  setTimeout(async () => {
    if (await isPortOpen(3000)) {
      console.log('react-ui already listening on http://127.0.0.1:3000');
      return;
    }

    run('react-ui', isWindows ? 'npm.cmd' : 'npm', ['run', 'start:react'], appDir, {
      BROWSER: process.env.BROWSER || 'none',
    });
  }, 1200);
})();
