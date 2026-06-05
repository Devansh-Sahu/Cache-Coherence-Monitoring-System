import axios from 'axios';

const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000';

const api = axios.create({
  baseURL: API_BASE,
  timeout: 15000,
});

export const fetchDashboard = () => api.get('/api/dashboard/');
export const fetchEvents = (keyName, hours = 24) =>
  api.get(`/api/events/${encodeURIComponent(keyName)}`, { params: { hours } });
export const fetchExplain = (keyName) =>
  api.get(`/api/events/${encodeURIComponent(keyName)}/explain`);
export const postAsk = (question) => api.post('/api/ask/', { question });
export const fetchRegistry = () => api.get('/api/registry/');

export default api;
