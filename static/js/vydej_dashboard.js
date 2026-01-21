// ============================================
// KLIKNIJIDLO - VÝDEJNÍ DASHBOARD (OPTIMALIZOVÁNO PRO RYCHLOST)
// ============================================

// 🔥 GLOBÁLNÍ PROMĚNNÉ
let currentRFIDOrder = null;
let socket = null;
let isConnected = false;
let lastRFIDTime = 0;

// ============================================
// HODINY
// ============================================
function updateTime() {
    const now = new Date();
    const hours = String(now.getHours()).padStart(2, '0');
    const minutes = String(now.getMinutes()).padStart(2, '0');
    const seconds = String(now.getSeconds()).padStart(2, '0');
    const timeElement = document.getElementById('liveTime');
    if (timeElement) {
        timeElement.textContent = `${hours}:${minutes}:${seconds}`;
    }
}

// ============================================
// TAB SWITCHING
// ============================================
function initTabs() {
    document.querySelectorAll('.tab-button').forEach(button => {
        button.addEventListener('click', function() {
            document.querySelectorAll('.tab-button').forEach(btn => btn.classList.remove('active'));
            this.classList.add('active');
            
            document.querySelectorAll('.tab-pane').forEach(pane => pane.classList.remove('active'));
            const tabId = this.getAttribute('data-tab');
            const targetTab = document.getElementById(tabId);
            if (targetTab) {
                targetTab.classList.add('active');
            }
        });
    });
}

// ============================================
// VYHLEDÁVÁNÍ ZÁKAZNÍKŮ
// ============================================
const searchInput = document.getElementById('customerSearchInput');
const clearSearchBtn = document.getElementById('clearSearchBtn');

function filterCustomers() {
    const searchTerm = searchInput ? searchInput.value.toLowerCase().trim() : '';
    const customerCards = document.querySelectorAll('#customers .customer-card');
    const noResultsMessage = document.getElementById('noResultsMessage');
    let visibleCount = 0;

    customerCards.forEach(card => {
        const userName = card.getAttribute('data-user-name');
        if (userName && userName.includes(searchTerm)) {
            card.style.display = '';
            visibleCount++;
        } else {
            card.style.display = 'none';
        }
    });

    if (noResultsMessage) {
        noResultsMessage.style.display = (visibleCount === 0 && searchTerm !== '') ? 'flex' : 'none';
    }
}

function initSearch() {
    if (searchInput) {
        searchInput.addEventListener('input', filterCustomers);
    }

    if (clearSearchBtn) {
        clearSearchBtn.addEventListener('click', () => {
            if (searchInput) {
                searchInput.value = '';
                filterCustomers();
                searchInput.focus();
            }
        });
    }
}

// ============================================
// RFID STATE MANAGEMENT
// ============================================
function showRFIDWaiting() {
    const waitingState = document.getElementById('rfidWaitingState');
    const successState = document.getElementById('rfidSuccessState');
    const errorState = document.getElementById('rfidErrorState');
    const loadingOverlay = document.getElementById('rfidLoadingOverlay');
    
    if (waitingState) waitingState.style.display = 'flex';
    if (successState) successState.style.display = 'none';
    if (errorState) errorState.style.display = 'none';
    if (loadingOverlay) loadingOverlay.style.display = 'none';
    
    currentRFIDOrder = null;
}

function showRFIDLoading() {
    const loadingOverlay = document.getElementById('rfidLoadingOverlay');
    if (loadingOverlay) {
        loadingOverlay.style.display = 'flex';
    }
}

function hideRFIDLoading() {
    const loadingOverlay = document.getElementById('rfidLoadingOverlay');
    if (loadingOverlay) {
        loadingOverlay.style.display = 'none';
    }
}

function hideRFIDStates() {
    const waitingState = document.getElementById('rfidWaitingState');
    const successState = document.getElementById('rfidSuccessState');
    const errorState = document.getElementById('rfidErrorState');
    
    if (waitingState) waitingState.style.display = 'none';
    if (successState) successState.style.display = 'none';
    if (errorState) errorState.style.display = 'none';
}

// ✅ KOMPAKTNÍ RENDERING POLOŽEK - SE TLAČÍTKY PRO JEDNOTLIVÉ VYDÁNÍ
function renderRFIDItems(items) {
    const container = document.getElementById('rfidOrderItems');
    if (!container) return;
    
    container.innerHTML = '';
    
    if (!items || items.length === 0) {
        container.innerHTML = '<p class="text-muted text-center">Žádné položky k výdeji</p>';
        return;
    }
    
    items.forEach(item => {
        const itemEl = document.createElement('div');
        itemEl.className = 'rfid-order-item-compact';
        
        // 🔥 Pokud je vydáno, přidej zelenou ikonku
        const issuedIcon = item.issued ? '<i class="fas fa-check-circle text-success me-2"></i>' : '';
        
        // 🔥 Pokud NENÍ vydáno, přidej tlačítko "Vydat"
        let actionButton = '';
        if (!item.issued && item.item_ids && item.item_ids.length > 0) {
            actionButton = `
                <button class="btn btn-sm btn-success btn-issue-single-item" 
                        data-item-ids="${item.item_ids.join(',')}"
                        data-item-name="${item.name}"
                        data-item-quantity="${item.quantity}"
                        title="Vydat tuto položku">
                    <i class="fas fa-check"></i>
                </button>
            `;
        }
        
        itemEl.innerHTML = `
            <div class="rfid-item-qty-compact">${item.quantity}×</div>
            <div class="rfid-item-details-compact">
                <div class="rfid-item-name-compact">
                    ${issuedIcon}${item.name}
                </div>
                <div class="rfid-item-type-compact">${item.type}</div>
            </div>
            ${actionButton}
        `;
        container.appendChild(itemEl);
    });
    
    // 🔥 NAVĚS LISTENERY NA TLAČÍTKA
    attachSingleItemIssueListeners();
}


// 🔥 NOVÝ STAV - UŽ VYDANÁ OBJEDNÁVKA
function showRFIDAlreadyIssued(orderData) {
    currentRFIDOrder = orderData;
    
    hideRFIDStates();
    
    const successState = document.getElementById('rfidSuccessState');
    if (successState) {
        successState.style.display = 'block';
    }
    
    // Naplň data
    const userNameEl = document.getElementById('rfidUserName');
    const orderDateEl = document.getElementById('rfidOrderDate');
    const scanTimeEl = document.getElementById('rfidScanTime');
    
    if (userNameEl) userNameEl.textContent = orderData.user_name || '-';
    if (orderDateEl) orderDateEl.textContent = orderData.order_date || '-';
    
    // Čas vydání (místo aktuálního času)
    if (scanTimeEl) {
        scanTimeEl.textContent = orderData.issued_time || '-';
    }
    
    // ✅ RENDER VYDANÝCH POLOŽEK
    renderRFIDItems(orderData.items);
    
    // 🔥 SKRYJ TLAČÍTKO "VYDAT JÍDLO"
    const issueIconBtn = document.getElementById('rfidIssueIconBtn');
    if (issueIconBtn) {
        issueIconBtn.style.display = 'none';
    }
    
    // Změň text cancelBtn na "OK" a přidej zelené pozadí
    const cancelBtn = document.getElementById('rfidCancelBtn');
    if (cancelBtn) {
        cancelBtn.innerHTML = '<i class="fas fa-check me-1"></i> OK';
        cancelBtn.classList.remove('btn-rfid-cancel-small');
        cancelBtn.classList.add('btn-success');
        cancelBtn.onclick = function() {
            showRFIDWaiting();
        };
    }
    
    // 🔥 AUTO-ZAVŘENÍ PO 5 SEKUNDÁCH
    setTimeout(() => {
        showRFIDWaiting();
    }, 5000);
    
    // Přepni na RFID tab
    const rfidTabBtn = document.querySelector('[data-tab="rfid"]');
    if (rfidTabBtn) {
        rfidTabBtn.click();
    }
}


function showRFIDSuccess(orderData) {
    currentRFIDOrder = orderData;
    
    hideRFIDStates();
    
    const successState = document.getElementById('rfidSuccessState');
    if (successState) {
        successState.style.display = 'block';
    }
    
    // Naplň data
    const userNameEl = document.getElementById('rfidUserName');
    const orderDateEl = document.getElementById('rfidOrderDate');
    const scanTimeEl = document.getElementById('rfidScanTime');
    
    if (userNameEl) userNameEl.textContent = orderData.user_name || '-';
    if (orderDateEl) orderDateEl.textContent = orderData.order_date || '-';
    
    // Aktuální čas načtení
    const now = new Date();
    if (scanTimeEl) {
        scanTimeEl.textContent = `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}:${String(now.getSeconds()).padStart(2, '0')}`;
    }
    
    // ✅ POUŽIJ KOMPAKTNÍ RENDERING
    renderRFIDItems(orderData.items);
    
    // ✅ NAVĚS LISTENER NA VELKOU ZELENOU FAJFKU
    const issueIconBtn = document.getElementById('rfidIssueIconBtn');
    if (issueIconBtn) {
        issueIconBtn.onclick = function() {
            issueRFIDOrder(orderData.order_id);
        };
    }
    
    // Cancel button
    const cancelBtn = document.getElementById('rfidCancelBtn');
    if (cancelBtn) {
        cancelBtn.onclick = function() {
            showRFIDWaiting();
        };
    }
    
    // Přepni na RFID tab
    const rfidTabBtn = document.querySelector('[data-tab="rfid"]');
    if (rfidTabBtn) {
        rfidTabBtn.click();
    }
}

function showRFIDError(errorMessage, rfidTag) {
    console.log('🔴 Showing error:', errorMessage); // Debug
    
    hideRFIDStates();
    
    const errorState = document.getElementById('rfidErrorState');
    if (errorState) {
        errorState.style.display = 'flex';
        errorState.style.zIndex = '1000'; // Nad loading
    }
    
    // Přidej třídu pro animaci
    if (errorState) errorState.classList.add('error-shown');
    
    // Přepni tab
    const rfidTabBtn = document.querySelector('[data-tab="rfid"]');
    if (rfidTabBtn) rfidTabBtn.click();
    
    // 🔥 FAIL-SAFE AUTO-HIDE (5s) + force waiting
    setTimeout(() => {
        console.log('🕒 Auto-hiding error');
        if (errorState) {
            errorState.style.display = 'none';
            errorState.classList.remove('error-shown');
        }
        showRFIDWaiting();
    }, 2500);
}

// ✅ FUNKCE PRO VYDÁNÍ OBJEDNÁVKY
function issueRFIDOrder(orderId) {
    if (!orderId) {
        console.error('❌ Order ID je prázdné');
        return;
    }
    
    showRFIDLoading();
    
    fetch(`/vydej/issue-order/${orderId}/`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': getCookie('csrftoken')
        }
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            showNotification('✅ ' + data.message, 'success');
            showRFIDWaiting();
            refreshDashboardData();
        } else {
            showRFIDError(data.error || 'Chyba při vydávání objednávky', currentRFIDOrder?.rfid_tag);
        }
    })
    .catch(error => {
        console.error('Error:', error);
        showRFIDError('Chyba při komunikaci se serverem', currentRFIDOrder?.rfid_tag);
    })
    .finally(() => {
        hideRFIDLoading();
    });
}

// ============================================
// RFID PROCESSING
// ============================================
async function processRFIDTag(rfidTag) {
    try {
        if (window.rfidProcessing) {
            console.log('⏳ Již probíhá zpracování RFID');
            return;
        }
        window.rfidProcessing = true;
        
        showRFIDLoading();
        
        const response = await fetch('/vydej/rfid-scan/', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': getCookie('csrftoken')
            },
            body: JSON.stringify({ rfid_tag: rfidTag })
        });
        
        const data = await response.json();
        
        if (data.success) {
            // Načti detail objednávky
            const orderResponse = await fetch(`/vydej/get-order-detail/${data.order_id}/`, {
                headers: {
                    'X-Requested-With': 'XMLHttpRequest'
                }
            });
            
            const orderData = await orderResponse.json();
            
            if (orderData.success) {
                orderData.rfid_tag = rfidTag;
                
                // 🔥 KONTROLA - JE UŽ VYDANÁ?
                if (orderData.already_issued) {
                    showRFIDAlreadyIssued(orderData);
                } else {
                    showRFIDSuccess(orderData);
                }
            } else {
                showRFIDError(orderData.error || 'Chyba načítání detailu objednávky', rfidTag);
            }
        } else {
            showRFIDError(data.error, rfidTag);
        }
        
    } catch (error) {
        console.error('RFID Error:', error);
        showRFIDError('Chyba sítě: ' + error.message, rfidTag);
    } finally {
        window.rfidProcessing = false;
        hideRFIDLoading();
    }
}


// ============================================
// MANUÁLNÍ VYDÁVÁNÍ Z TABU ZÁKAZNÍKŮ
// ============================================
function attachIssueOrderListeners() {
    document.querySelectorAll('.btn-issue-order').forEach(button => {
        button.addEventListener('click', function(e) {
            e.preventDefault();
            
            const orderId = this.getAttribute('data-order-id');
            const card = this.closest('.customer-card');
            
            this.disabled = true;
            this.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Vydávám...';
            
            fetch(`/vydej/issue-order/${orderId}/`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCookie('csrftoken')
                }
            })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    // ⚡ OKAMŽITÉ ODSTRANĚNÍ (bez animace)
                    card.remove();
                    refreshDashboardData();
                    showNotification('✅ Objednávka úspěšně vydána!', 'success');
                } else {
                    showNotification('❌ ' + (data.error || 'Chyba při vydávání objednávky'), 'error');
                    this.disabled = false;
                    this.innerHTML = '<i class="fas fa-check"></i> Vydat';
                }
            })
            .catch(error => {
                console.error('Error:', error);
                showNotification('❌ Chyba při komunikaci se serverem', 'error');
                this.disabled = false;
                this.innerHTML = '<i class="fas fa-check"></i> Vydat';
            });
        });
    });
}

// ============================================
// REFRESH DASHBOARD DATA
// ============================================
function refreshDashboardData() {
    fetch('/vydej/refresh-data/', {
        method: 'GET',
        headers: {
            'X-Requested-With': 'XMLHttpRequest'
        }
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            updateBadgeCounts(data.pending_count, data.completed_count);
            updatePendingOrders(data.pending_orders_html);
            updateCompletedOrders(data.completed_orders_html);
            updateSummaryFooter(data.summary_html);
            updateRecentOrders(data.recent_orders_html);
            attachIssueOrderListeners();
        }
    })
    .catch(error => {
        console.error('Error refreshing dashboard:', error);
    });
}

function updateBadgeCounts(pendingCount, completedCount) {
    const pendingBadge = document.querySelector('[data-tab="customers"] .tab-badge');
    const completedBadge = document.querySelector('[data-tab="completed"] .tab-badge');
    
    if (pendingBadge) pendingBadge.textContent = pendingCount;
    if (completedBadge) completedBadge.textContent = completedCount;
}

function updatePendingOrders(html) {
    const customersTab = document.getElementById('customers');
    if (!customersTab) return;
    
    const gridElement = customersTab.querySelector('.customer-grid');
    const emptyState = customersTab.querySelector('.empty-state:not(#noResultsMessage)');
    
    if (html.trim() === '') {
        if (gridElement) gridElement.remove();
        if (!emptyState) {
            const searchFilter = customersTab.querySelector('.search-filter');
            const emptyDiv = document.createElement('div');
            emptyDiv.className = 'empty-state';
            emptyDiv.innerHTML = `
                <i class="fas fa-check-circle"></i>
                <p>Žádné objednávky k výdeji</p>
            `;
            if (searchFilter) {
                searchFilter.after(emptyDiv);
            } else {
                customersTab.appendChild(emptyDiv);
            }
        }
    } else {
        if (emptyState) emptyState.remove();
        if (gridElement) {
            gridElement.innerHTML = html;
        } else {
            const searchFilter = customersTab.querySelector('.search-filter');
            const newGrid = document.createElement('div');
            newGrid.className = 'customer-grid';
            newGrid.innerHTML = html;
            if (searchFilter) {
                searchFilter.after(newGrid);
            } else {
                customersTab.insertBefore(newGrid, customersTab.firstChild);
            }
        }
    }
    
    if (searchInput && searchInput.value) {
        filterCustomers();
    }
}

function updateCompletedOrders(html) {
    const completedTab = document.getElementById('completed');
    if (!completedTab) return;
    
    const gridElement = completedTab.querySelector('.customer-grid');
    const emptyState = completedTab.querySelector('.empty-state');
    
    if (html.trim() === '') {
        if (gridElement) gridElement.remove();
        if (!emptyState) {
            const emptyDiv = document.createElement('div');
            emptyDiv.className = 'empty-state';
            emptyDiv.innerHTML = `
                <i class="fas fa-info-circle"></i>
                <p>Zatím nebylo nic vydáno</p>
            `;
            completedTab.appendChild(emptyDiv);
        }
    } else {
        if (emptyState) emptyState.remove();
        if (gridElement) {
            gridElement.innerHTML = html;
        } else {
            const newGrid = document.createElement('div');
            newGrid.className = 'customer-grid';
            newGrid.innerHTML = html;
            completedTab.appendChild(newGrid);
        }
    }
}

function updateSummaryFooter(html) {
    const summaryFooter = document.querySelector('.summary-footer');
    if (summaryFooter) {
        summaryFooter.innerHTML = html;
    }
}

function updateRecentOrders(html) {
    const recentList = document.querySelector('.recent-list');
    if (recentList) {
        recentList.innerHTML = html;
    }
}

// ============================================
// UTILITY FUNKCE
// ============================================
function getCookie(name) {
    const value = document.cookie
        .split(';')
        .map(c => c.trim())
        .find(c => c.startsWith(name + '='));
    if (!value) {
        console.warn('CSRF cookie not found for', name, 'in', document.cookie);
        return null;
    }
    return decodeURIComponent(value.split('=')[1]);
}


function showNotification(message, type = 'success') {
    const notification = document.createElement('div');
    notification.className = `alert alert-${type === 'success' ? 'success' : 'danger'} notification-toast`;
    notification.style.cssText = `
        position: fixed;
        top: 20px;
        right: 20px;
        z-index: 9999;
        min-width: 350px;
        font-size: 1.1rem;
        animation: slideIn 0.2s ease;
        box-shadow: 0 6px 20px rgba(0,0,0,0.2);
    `;
    notification.innerHTML = `
        <i class="fas fa-${type === 'success' ? 'check-circle' : 'exclamation-circle'} me-2"></i>
        ${message}
    `;
    
    document.body.appendChild(notification);
    
    // ⚡ RYCHLEJŠÍ ZMIZENÍ (1.5s místo 3s)
    setTimeout(() => {
        notification.style.animation = 'slideOut 0.2s ease';
        setTimeout(() => notification.remove(), 200);
    }, 1500);
}

// ============================================
// RFID BRIDGE CONNECTION
// ============================================
function connectRFIDBridge() {
    const btn = document.getElementById('connectRFIDBtn');
    const disconnectBtn = document.getElementById('disconnectRFIDBtn');
    
    if (socket && isConnected) {
        console.log('⚠️ Bridge již připojen');
        return;
    }
    
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<i class="fas fa-spinner fa-spin me-2"></i> Připojuji...';
    }
    
    console.log('🔌 Připojuji k RFID Bridge na jidelna.kliknijidlo.cz...');
    
    try {
        socket = io('http://localhost:3001', {
        transports: ['websocket', 'polling'],
        timeout: 5000,
        reconnection: true,
        reconnectionAttempts: 5,
        reconnectionDelay: 500,
        forceNew: true
    });
        
        socket.on('connect', () => {
            console.log('✅ Bridge připojen! Transport:', socket.io.engine.transport.name);
            isConnected = true;
            
            if (btn) btn.style.display = 'none';
            if (disconnectBtn) disconnectBtn.style.display = 'inline-block';
            
            showNotification('✅ RFID čtečka připojena', 'success');
        });
        
        socket.on('rfid_scan', (data) => {
            console.log('📡 Event: rfid_scan ->', data);
            handleRFIDScan(data.rfid_tag || data.rfid);
        });
        
        socket.on('rfid_scanned', (data) => {
            console.log('📡 Event: rfid_scanned ->', data);
            handleRFIDScan(data.rfid_tag || data.rfid);
        });
        
        socket.on('status', (data) => {
            console.log('📊 Bridge status:', data);
        });
        
        socket.on('disconnect', (reason) => {
            console.log('❌ Bridge odpojen:', reason);
            isConnected = false;
            
            if (btn) {
                btn.style.display = 'inline-block';
                btn.disabled = false;
                btn.innerHTML = '<i class="fas fa-plug me-2"></i> Připojit Bridge';
            }
            if (disconnectBtn) {
                disconnectBtn.style.display = 'none';
            }
        });
        
        socket.on('connect_error', (error) => {
            console.error('❌ Chyba připojení k Bridge:', error.message);
            isConnected = false;
            
            if (btn) {
                btn.disabled = false;
                btn.innerHTML = '<i class="fas fa-plug me-2"></i> Připojit Bridge';
            }
            
            showNotification('❌ Nelze se připojit k RFID bridge na localhost:3001', 'error');
        });
        
        socket.io.on('reconnect', (attempt) => {
            console.log(`🔄 Znovu připojeno po ${attempt} pokusech`);
            showNotification('🔄 RFID čtečka znovu připojena', 'success');
        });
        
    } catch (error) {
        console.error('💥 Chyba inicializace Socket.IO:', error);
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = '<i class="fas fa-plug me-2"></i> Připojit Bridge';
        }
        showNotification('❌ Chyba: ' + error.message, 'error');
    }
}

function handleRFIDScan(rfidTag) {
    if (!rfidTag) {
        console.error('❌ RFID tag je prázdný!');
        return;
    }
    
    const now = Date.now();
    // ⚡ ZKRÁCENO z 2000ms na 300ms (rychlejší opakování)
    if (now - lastRFIDTime < 300) {
        console.log('⏭️ Duplicita ignorována (cooldown 0.3s)');
        return;
    }
    lastRFIDTime = now;
    
    console.log('🎯 Zpracovávám RFID:', rfidTag);
    processRFIDTag(rfidTag);
}

function disconnectRFIDBridge() {
    if (socket) {
        socket.disconnect();
        socket = null;
        isConnected = false;
        console.log('🔌 Bridge manuálně odpojen');
    }
    
    const btn = document.getElementById('connectRFIDBtn');
    const disconnectBtn = document.getElementById('disconnectRFIDBtn');
    
    if (btn) btn.style.display = 'inline-block';
    if (disconnectBtn) disconnectBtn.style.display = 'none';
}

// ============================================
// VYDÁNÍ JEDNOTLIVÉ POLOŽKY
// ============================================
function attachSingleItemIssueListeners() {
    document.querySelectorAll('.btn-issue-single-item').forEach(button => {
        button.addEventListener('click', function(e) {
            e.preventDefault();
            e.stopPropagation();
            
            const itemIds = this.getAttribute('data-item-ids').split(',');
            const itemName = this.getAttribute('data-item-name');
            const itemQuantity = this.getAttribute('data-item-quantity');
            
            // Disable tlačítko
            this.disabled = true;
            this.innerHTML = '<i class="fas fa-spinner fa-spin"></i>';
            
            // Vydej všechny položky v této skupině (pokud jich je víc)
            issueSingleItemGroup(itemIds, itemName, itemQuantity, this);
        });
    });
}

async function issueSingleItemGroup(itemIds, itemName, itemQuantity, button) {
    try {
        let allSuccess = true;
        
        // Vydej postupně všechny položky
        for (const itemId of itemIds) {
            const response = await fetch(`/vydej/issue-item/${itemId}/`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCookie('csrftoken')
                }
            });
            
            const data = await response.json();
            
            if (!data.success) {
                allSuccess = false;
                showNotification('❌ ' + data.error, 'error');
                break;
            }
        }
        
        if (allSuccess) {
            showNotification(`✅ Vydáno: ${itemQuantity}× ${itemName}`, 'success');
            
            // Refresh detailu objednávky
            if (currentRFIDOrder && currentRFIDOrder.order_id) {
                const orderResponse = await fetch(`/vydej/get-order-detail/${currentRFIDOrder.order_id}/`, {
                    headers: {
                        'X-Requested-With': 'XMLHttpRequest'
                    }
                });
                
                const orderData = await orderResponse.json();
                
                if (orderData.success) {
                    // Pokud jsou všechny položky vydané, zobraz jako "už vydáno"
                    if (orderData.already_issued || orderData.items.length === 0) {
                        setTimeout(() => {
                            showRFIDWaiting();
                            refreshDashboardData();
                        }, 2000);
                    } else {
                        // Jinak refresh zobrazení
                        renderRFIDItems(orderData.items);
                    }
                }
            }
        } else {
            // Chyba - obnov tlačítko
            button.disabled = false;
            button.innerHTML = '<i class="fas fa-check"></i>';
        }
        
    } catch (error) {
        console.error('Error issuing item:', error);
        showNotification('❌ Chyba při vydávání položky', 'error');
        button.disabled = false;
        button.innerHTML = '<i class="fas fa-check"></i>';
    }
}


// ============================================
// INICIALIZACE
// ============================================
function initDashboard() {
    console.log('🚀 Inicializuji RFID Dashboard...');
    
    // Spusť hodiny
    updateTime();
    setInterval(updateTime, 1000);
    
    // Inicializuj taby
    initTabs();
    
    // Inicializuj vyhledávání
    initSearch();
    
    // Navěs listenery na tlačítka vydání
    attachIssueOrderListeners();
    
    // ⚡ Auto-refresh každých 10 sekund (místo 30)
    setInterval(() => {
        if (!searchInput || !searchInput.value) {
            refreshDashboardData();
        }
    }, 10000);
    
    // ⚡ OKAMŽITÉ AUTO-CONNECT (bez setTimeout)
    console.log('⏱️ Spouštím auto-connect k RFID Bridge...');
    connectRFIDBridge();
    
    const shutdownBtn = document.getElementById('shutdownBtn');
    if (shutdownBtn) {
        shutdownBtn.addEventListener('click', () => {
            if (confirm('Opravdu vypnout výdej?\n\nProhlížeč se zavře.')) {
                // 1. FULLSCREEN EXIT
                if (document.exitFullscreen) {
                    document.exitFullscreen();
                } else if (document.webkitExitFullscreen) {
                    document.webkitExitFullscreen();
                } else if (document.mozCancelFullScreen) {
                    document.mozCancelFullScreen();
                }
                
                // 2. Zavři okno (funguje v kiosk mode)
                window.close();
                
                // 3. Fallback - domovská stránka
                setTimeout(() => {
                    window.location.href = '/';
                }, 500);
            }
        });
    }

    console.log('✅ Dashboard initialized');
}

// Spusť po načtení DOM
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initDashboard);
} else {
    initDashboard();
}

// ============================================
// DEBUG FUNKCE
// ============================================
window.testRFID = function(rfidTag = '2404211AFFFF12E0') {
    console.log('🧪 TEST: Simuluji RFID scan');
    processRFIDTag(rfidTag);
};

console.log('📡 RFID Dashboard script loaded');
