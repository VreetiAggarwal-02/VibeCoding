document.addEventListener('DOMContentLoaded', function() {
    const BACKEND_URL = "http://localhost:8001";
    const startBtn = document.getElementById('start-btn');
    if (startBtn) {
        startBtn.addEventListener('click', function() {
            console.log('Start button clicked');
            // Button color change handled by CSS :active
        });
    }

    const uploadForm = document.getElementById('upload-form');
    const pdfInput = document.getElementById('pdf-input');
    const formSection = document.getElementById('form-section');
    const reviewForm = document.getElementById('review-form');
    const fieldsContainer = document.getElementById('fields-container');
    const messageDiv = document.getElementById('message');
    const taxComparisonDiv = document.getElementById('tax-comparison');
    const chatSection = document.getElementById('chat-section');
    const chatHistoryDiv = document.getElementById('chat-history');
    const chatForm = document.getElementById('chat-form');
    const chatInput = document.getElementById('chat-input');
    let chatHistory = [];

    let sessionId = null;
    let extractedData = null;

    if (uploadForm) {
        uploadForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            messageDiv.textContent = '';
            const file = pdfInput.files[0];
            if (!file) {
                messageDiv.textContent = 'Please select a PDF file.';
                return;
            }
            const formData = new FormData();
            formData.append('pdf', file);
            try {
                console.log('Uploading PDF to /api/upload-pdf', file);
                const res = await fetch(`${BACKEND_URL}/api/upload-pdf`, {
                    method: 'POST',
                    body: formData
                });
                console.log('Received response from /api/upload-pdf', res);
                if (!res.ok) throw new Error('Failed to upload and extract PDF.');
                const data = await res.json();
                console.log('Extracted data:', data);
                sessionId = data.session_id;
                extractedData = data.extracted_data;
                showReviewForm(extractedData);
                formSection.style.display = 'block';
            } catch (err) {
                console.error('Error during PDF upload:', err);
                messageDiv.textContent = err.message;
            }
        });
    }

    function showReviewForm(data) {
        fieldsContainer.innerHTML = '';
        const fields = [
            'gross_salary', 'basic_salary', 'hra_received', 'rent_paid',
            'deduction_80c', 'deduction_80d', 'standard_deduction',
            'professional_tax', 'tds'
        ];
        fields.forEach(field => {
            const value = data[field] !== undefined ? data[field] : '';
            const label = field.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase());
            const div = document.createElement('div');
            div.innerHTML = `<label>${label}: <input type="number" step="0.01" name="${field}" value="${value}" required></label>`;
            fieldsContainer.appendChild(div);
        });
        // Set tax regime if present
        if (data.tax_regime) {
            reviewForm.elements['tax_regime'].value = data.tax_regime;
        }
    }

    if (reviewForm) {
        reviewForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            messageDiv.textContent = '';
            if (!sessionId) {
                messageDiv.textContent = 'No session found. Please upload a PDF first.';
                return;
            }
            const formData = new FormData(reviewForm);
            const reviewedData = {};
            for (const [key, value] of formData.entries()) {
                reviewedData[key] = value;
            }
            try {
                console.log(`Submitting reviewed data to /api/session/${sessionId}/review`, reviewedData);
                const res = await fetch(`${BACKEND_URL}/api/session/${sessionId}/review`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(reviewedData)
                });
                console.log('Received response from /api/session/{sessionId}/review', res);
                if (!res.ok) throw new Error('Failed to submit reviewed data.');
                messageDiv.textContent = 'Data submitted successfully! Calculating tax...';
                // Call tax calculation endpoint
                const calcRes = await fetch(`${BACKEND_URL}/api/calculate-tax`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ session_id: sessionId, data: reviewedData })
                });
                if (!calcRes.ok) throw new Error('Failed to calculate tax.');
                const calcData = await calcRes.json();
                showTaxComparison(calcData);
                formSection.style.display = 'none';
                uploadForm.reset();
            } catch (err) {
                console.error('Error during review submission or tax calculation:', err);
                messageDiv.textContent = err.message;
            }
        });
    }

    function showTaxComparison(calcData) {
        if (!calcData || !calcData.old_regime || !calcData.new_regime) {
            taxComparisonDiv.style.display = 'none';
            return;
        }
        const oldR = calcData.old_regime;
        const newR = calcData.new_regime;
        taxComparisonDiv.innerHTML = `
            <h2>Tax Regime Comparison</h2>
            <div style="display: flex; gap: 2rem; justify-content: center;">
                <div class="tax-card" style="border:2px solid #1976d2; border-radius:8px; padding:1rem; width:250px; background:#f5faff;">
                    <h3>Old Regime</h3>
                    <p><strong>Taxable Income:</strong> ₹${oldR.taxable_income.toLocaleString()}</p>
                    <p><strong>Total Tax:</strong> ₹${oldR.total_tax.toLocaleString()}</p>
                    <p><strong>Deductions:</strong> ₹${oldR.deductions.toLocaleString()}</p>
                    <p><strong>Net Tax Payable:</strong> ₹${oldR.net_tax_payable.toLocaleString()}</p>
                </div>
                <div class="tax-card" style="border:2px solid #43a047; border-radius:8px; padding:1rem; width:250px; background:#f5fff7;">
                    <h3>New Regime</h3>
                    <p><strong>Taxable Income:</strong> ₹${newR.taxable_income.toLocaleString()}</p>
                    <p><strong>Total Tax:</strong> ₹${newR.total_tax.toLocaleString()}</p>
                    <p><strong>Deductions:</strong> ₹${newR.deductions.toLocaleString()}</p>
                    <p><strong>Net Tax Payable:</strong> ₹${newR.net_tax_payable.toLocaleString()}</p>
                </div>
            </div>
        `;
        taxComparisonDiv.style.display = 'block';
        // Start chat after showing tax comparison
        startChat();
    }

    async function startChat() {
        chatSection.style.display = 'block';
        chatHistory = [];
        chatHistoryDiv.innerHTML = '<em>Loading AI advisor...</em>';
        // Call backend to get Gemini's first message
        try {
            const res = await fetch(`${BACKEND_URL}/api/chat`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ session_id: sessionId, chat_history: [], user_data: extractedData })
            });
            if (!res.ok) throw new Error('Failed to start chat.');
            const data = await res.json();
            chatHistory = data.chat_history;
            renderChatHistory();
        } catch (err) {
            chatHistoryDiv.innerHTML = `<span style="color:red;">${err.message}</span>`;
        }
    }

    function renderChatHistory() {
        chatHistoryDiv.innerHTML = '';
        chatHistory.forEach(msg => {
            if (msg.role === 'user') {
                chatHistoryDiv.innerHTML += `<div style="text-align:right;"><span style="background:#1976d2;color:#fff;padding:6px 12px;border-radius:16px;display:inline-block;margin:2px 0;">${msg.content}</span></div>`;
            } else if (msg.role === 'assistant') {
                chatHistoryDiv.innerHTML += `<div style="text-align:left;"><span style="background:#e3f2fd;color:#222;padding:6px 12px;border-radius:16px;display:inline-block;margin:2px 0;">${msg.content}</span></div>`;
            }
        });
        chatHistoryDiv.scrollTop = chatHistoryDiv.scrollHeight;
    }

    if (chatForm) {
        chatForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            const userMsg = chatInput.value.trim();
            if (!userMsg) return;
            chatInput.value = '';
            // Add user message to chat history
            chatHistory.push({ role: 'user', content: userMsg });
            renderChatHistory();
            // Call backend for Gemini's response
            try {
                const res = await fetch(`${BACKEND_URL}/api/chat`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ session_id: sessionId, user_message: userMsg, chat_history: chatHistory, user_data: extractedData })
                });
                if (!res.ok) throw new Error('Failed to get AI response.');
                const data = await res.json();
                chatHistory = data.chat_history;
                renderChatHistory();
            } catch (err) {
                chatHistoryDiv.innerHTML += `<div style="color:red;">${err.message}</div>`;
            }
        });
    }
}); 