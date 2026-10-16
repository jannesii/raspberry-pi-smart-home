// — Socket.IO setup using cookie auth —
console.log('🛠️ Initializing Socket.IO with session cookie');
// Expose a single shared socket instance on window
window.socket = io('/', {
    transports: ['websocket'],
    auth: { role: 'view' },
    reconnection: true,
    reconnectionAttempts: Infinity,
    reconnectionDelay: 1000,
    reconnectionDelayMax: 10000,
    timeout: 20000
});

window.addEventListener('beforeunload', () => {
    window.socket && window.socket.disconnect();
});

window.socket.on('connect_error', err => {
    console.error('Connection error:', err);
});

window.socket.on('connect', () => {
    console.log('✅ Yhdistetty palvelimeen');
})

window.socket.on('disconnect', reason => {
    console.warn('Socket disconnected:', reason);
});

window.socket.on('server_shutdown', () => {
    console.log('🔒 Server is shutting down, waiting for reconnect...');
});
