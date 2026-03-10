// Socket.IO client pro RFID Bridge - CORS FIXED
let socket = null;
let isConnected = false;
let reconnectAttempts = 0;

function connectRFIDBridge() {
    if (isConnected) return;
    
    console.log('🔌 Připojuji k RFID Bridge...');
    
    // ✅ ROBUSTNÍ KONEKCE s fallbacky
    socket = io('http://10.0.0.108:3001', {
        transports: ['websocket', 'polling'],
        timeout: 10000,
        reconnection: true,
        reconnectionAttempts: 5,
        reconnectionDelay: 1000,
        reconnectionDelayMax: 5000,
        forceNew: true
    });
    
    socket.on('connect', () => {
        console.log('✅ Připojeno k Bridge!', socket.io.engine.transport.name);
        isConnected = true;
        reconnectAttempts = 0;
        updateRFIDStatus('Připojeno ✅', 'success');
        toggleRFIDButtons(true);
    });
    
    socket.on('rfid_scan', async (data) => {
        console.log('🆔 RFID detekován:', data.rfid_tag);
        showRFIDNotification(data.rfid_tag);
        await processRFIDTag(data.rfid_tag);
    });
    
    socket.on('status', (data) => {
        updateRFIDStatus(`Port: ${data.port_open ? 'ON' : 'OFF'}`, 
                        data.port_open ? 'success' : 'warning');
    });
    
    socket.on('connect_error', (err) => {
        console.log('🔌 Connect error:', err.message);
        reconnectAttempts++;
        updateRFIDStatus(`Chyba ${reconnectAttempts}/5`, 'danger');
    });
    
    socket.on('disconnect', (reason) => {
        console.log('❌ Odpojeno:', reason);
        isConnected = false;
        updateRFIDStatus(`Odpojeno: ${reason}`, 'danger');
        toggleRFIDButtons(false);
    });
    
    // Auto-reconnect status
    socket.io.on('reconnect_attempt', (attempt) => {
        console.log(`🔄 Reconnect pokus ${attempt}`);
    });
    
    socket.io.on('reconnect', (attempt) => {
        console.log(`✅ Reconnected po ${attempt} pokusech`);
    });
}

function disconnectRFIDBridge() {
    if (socket) {
        console.log('🔌 Ruční odpojení...');
        socket.disconnect();
        socket = null;
    }
    isConnected = false;
    updateRFIDStatus('Ručně odpojeno', 'warning');
    toggleRFIDButtons(false);
}

function toggleRFIDButtons(connected) {
    const connectBtn = document.querySelector('#connectRFIDBtn');
    const disconnectBtn = document.querySelector('#disconnectRFIDBtn');
    
    if (connectBtn) connectBtn.style.display = connected ? 'none' : 'inline-block';
    if (disconnectBtn) disconnectBtn.style.display = connected ? 'inline-block' : 'none';
}

function updateRFIDStatus(message, type = 'info') {
    const statusEl = document.getElementById('rfidStatus');
    if (statusEl) {
        statusEl.textContent = message;
        statusEl.className = `mt-2 p-2 bg-${type === 'success' ? 'success' : 
                                        type === 'danger' ? 'danger' : 
                                        type === 'warning' ? 'warning' : 'light'} 
                                        text-${type === 'light' ? 'dark' : 'white'} rounded`;
    }
}

function showRFIDNotification(rfid) {
    // Bezpečné zobrazení posledních 8 znaků
    const shortRFID = rfid.slice(-8).toUpperCase();
    showNotification(`RFID: ${shortRFID}`, 'info');
}

// Inicializace při načtení stránky
document.addEventListener('DOMContentLoaded', () => {
    const connectBtn = document.getElementById('connectRFIDBtn');
    if (connectBtn) {
        connectBtn.addEventListener('click', connectRFIDBridge);
    }
    
    const disconnectBtn = document.getElementById('disconnectRFIDBtn');
    if (disconnectBtn) {
        disconnectBtn.addEventListener('click', disconnectRFIDBridge);
    }
});

let lastRFIDTime = 0;
socket.on('rfid_scan', async (data) => {
    const now = Date.now();
    if (now - lastRFIDTime < 2000) {  // 2s cooldown
        console.log('⏭️ Duplicita - skip');
        return;
    }
    lastRFIDTime = now;
    
    console.log('🆔 RFID:', data.rfid_tag);
    showRFIDNotification(data.rfid_tag);
    await processRFIDTag(data.rfid_tag);
});
