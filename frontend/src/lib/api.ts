import axios from 'axios';

function getUserId(): string {
  let uid = localStorage.getItem('x-user-id');
  if (!uid) {
    uid = 'web-' + crypto.randomUUID();
    localStorage.setItem('x-user-id', uid);
  }
  return uid;
}

const api = axios.create({ baseURL: '/api', timeout: 300000 });

api.interceptors.request.use((config) => {
  config.headers['X-User-ID'] = getUserId();
  return config;
});

api.interceptors.response.use(
  (r) => r,
  async (err) => {
    const config = err.config;
    config._retryCount = config._retryCount || 0;
    const shouldRetry = (!err.response || [502, 503, 504].includes(err.response.status)) && config._retryCount < 5;
    if (shouldRetry) {
      config._retryCount += 1;
      await new Promise(r => setTimeout(r, config._retryCount * 1500));
      return api(config);
    }
    if (err.response?.status === 401) {
      localStorage.removeItem('token');
      sessionStorage.removeItem('token');
    }
    return Promise.reject(err);
  },
);

export default api;
