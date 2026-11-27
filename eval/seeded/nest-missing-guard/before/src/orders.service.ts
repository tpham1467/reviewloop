import { Injectable } from '@nestjs/common';

@Injectable()
export class OrdersService {
  private readonly orders = new Map<string, string>();

  find(id: string): string | undefined {
    return this.orders.get(id);
  }

  remove(id: string): boolean {
    return this.orders.delete(id);
  }
}
