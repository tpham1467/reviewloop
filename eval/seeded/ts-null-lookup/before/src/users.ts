export interface User { id: string; email: string }

const users = new Map<string, User>();

export function add(u: User): void {
  users.set(u.id, u);
}
