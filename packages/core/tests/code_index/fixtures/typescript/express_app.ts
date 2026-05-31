import { Router, Request, Response } from 'express';

const router = Router();

router.get('/api/users', async (req: Request, res: Response) => {
    const users = await getUsers();
    res.json(users);
});

router.post('/api/users/:id', async (req: Request, res: Response) => {
    const result = await saveUser(req.params.id, req.body);
    res.json(result);
});

function listOrders(req: Request, res: Response) {
    const orders = getOrders();
    res.json(orders);
}

async function getUsers(): Promise<any[]> {
    return db.query('SELECT * FROM users');
}

async function saveUser(id: string, data: any): Promise<any> {
    return db.update('users', id, data);
}

function getOrders(): any[] {
    return [];
}
