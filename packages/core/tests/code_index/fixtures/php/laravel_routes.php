<?php

use Illuminate\Support\Facades\Route;

Route::get('/api/users', function () {
    return getUsers();
});

Route::post('/api/users/{id}', function ($id) {
    $data = request()->json()->all();
    return saveUser($id, $data);
});

function getUsers() {
    return DB::select('SELECT * FROM users');
}

function saveUser($id, $data) {
    return DB::table('users')->where('id', $id)->update($data);
}

class OrderController {
    public function listOrders() {
        return $this->getOrders();
    }

    private function getOrders() {
        return [];
    }
}
