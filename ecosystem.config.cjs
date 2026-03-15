module.exports = {
  apps: [
    {
      name: 'ghost-in-the-shell',
      cwd: '/data/ai4life/projects/ghost-in-the-shell',
      script: '/data/ai4life/projects/ghost-in-the-shell/start.sh',
      interpreter: 'none',
      autorestart: true,
      restart_delay: 5000,
      max_restarts: 10,
      env: {
        PYTHONUNBUFFERED: '1',
      },
    },
  ],
};
