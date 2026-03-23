module.exports = {
  apps: [
    {
      name: 'ghost-in-the-shell',
      cwd: __dirname,
      script: `${__dirname}/start.sh`,
      interpreter: 'none',
      autorestart: true,
      restart_delay: 5000,
      max_restarts: 10,
      env: {
        PYTHONUNBUFFERED: '1',
      },
    },
    {
      name: 'ghost-site',
      cwd: `${__dirname}/site`,
      script: 'npx',
      args: 'serve -l 9191',
      interpreter: 'none',
      autorestart: true,
      restart_delay: 3000,
    },
  ],
};
