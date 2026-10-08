import { apiClient } from './client';

export const policyApi = {
  list: () => apiClient.get('/api/v1/policies').then(r => r.data),
  getActive: () => apiClient.get('/api/v1/policies/active').then(r => r.data),
  create: (configuration: Record<string, unknown>) => apiClient.post('/api/v1/policies', { configuration }).then(r => r.data),
  activate: (id: string) => apiClient.post(`/api/v1/policies/${id}/activate`).then(r => r.data),
};
