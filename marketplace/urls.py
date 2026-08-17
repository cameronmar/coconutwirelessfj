from django.urls import path
from . import views
from . import social_views

urlpatterns = [
    # Static
    path('',                       views.home,          name='home'),
    path('how-it-works/',          views.how_it_works,  name='how_it_works'),
    path('privacy/',               views.privacy,       name='privacy'),
    path('terms/',                 views.terms,         name='terms'),
    path('support/',               views.contact_support, name='contact_support'),
    # Auth
    path('register/',              views.register_client,  name='register_client'),
    path('register/tradie/',       views.register_tradie,  name='register_tradie'),
    path('login/',                 views.login_view,    name='login'),
    path('logout/',                views.logout_view,   name='logout'),
    # Facebook Login
    path('integrations/facebook/connect/',   social_views.facebook_connect,  name='facebook_connect'),
    path('integrations/facebook/callback/',  social_views.facebook_callback, name='facebook_callback'),
    path('integrations/meta/data-deletion/', social_views.meta_data_deletion, name='meta_data_deletion'),
    path('integrations/meta/data-deletion/status/<str:confirmation_code>/', social_views.meta_data_deletion_status, name='meta_data_deletion_status'),
    path('password-reset/',        views.password_reset_request, name='password_reset_request'),
    path('password-reset/<str:uidb64>/<str:token>/', views.password_reset_confirm, name='password_reset_confirm'),
    path('account/change-password/', views.change_password, name='change_password'),
    path('dashboard/',             views.dashboard,     name='dashboard'),
    # Dashboards
    path('dashboard/client/',      views.client_dashboard, name='client_dashboard'),
    path('dashboard/tradie/',      views.tradie_dashboard, name='tradie_dashboard'),
    path('dashboard/tradie/service-area/', views.edit_service_area, name='edit_service_area'),
    path('billing/',               views.billing,          name='billing'),
    # Tasks
    path('tasks/',                 views.browse_tasks,  name='browse_tasks'),
    path('tasks/post/',            views.post_task,     name='post_task'),
    path('tasks/<int:pk>/',        views.task_detail,   name='task_detail'),
    path('tasks/<int:pk>/edit-dates/', views.edit_task_dates, name='edit_task_dates'),
    path('tasks/<int:pk>/quote/',  views.submit_quote,  name='submit_quote'),
    path('tasks/<int:pk>/quote/check-promo/', views.check_promo_code, name='check_promo_code'),
    path('tasks/<int:pk>/quoting-appointment/', views.book_quoting_appointment, name='book_quoting_appointment'),
    path('tasks/<int:pk>/quoting-appointment/<int:appt_pk>/accept/<int:slot_pk>/', views.accept_quoting_appointment_slot, name='accept_quoting_appointment_slot'),
    path('tasks/<int:pk>/quoting-appointment/<int:appt_pk>/decline/', views.decline_quoting_appointment, name='decline_quoting_appointment'),
    path('tasks/<int:pk>/quoting-appointment/<int:appt_pk>/cancel/', views.cancel_quoting_appointment, name='cancel_quoting_appointment'),
    path('tasks/<int:pk>/quotes/<int:qpk>/accept/', views.accept_quote, name='accept_quote'),
    path('tasks/<int:pk>/complete/', views.complete_task, name='complete_task'),
    # Reviews
    path('tasks/<int:pk>/rate/tradie/', views.rate_tradie, name='rate_tradie'),
    path('tasks/<int:pk>/rate/client/', views.rate_client, name='rate_client'),
    # Profile
    path('profile/<int:pk>/',      views.tradie_profile, name='tradie_profile'),
    path('professionals/',         views.browse_tradies, name='browse_tradies'),
    # Notices
    path('notices/',                         views.notices,      name='notices'),
    path('notices/settings/',                views.notification_settings, name='notification_settings'),
    # Messages
    path('messages/',                        views.inbox,        name='inbox'),
    path('messages/<int:tpk>/<int:opk>/',    views.conversation, name='conversation'),
    # Market
    path('market/',                          views.market_browse,          name='market_browse'),
    path('market/post/',                     views.create_market_listing,  name='create_market_listing'),
    path('market/mine/',                     views.my_market_listings,     name='my_market_listings'),
    path('market/orders/',                   views.my_market_orders,       name='my_market_orders'),
    path('market/orders/<int:pk>/cancel/',   views.market_order_cancel,    name='market_order_cancel'),
    path('market/orders/<int:pk>/<str:action>/', views.market_order_respond, name='market_order_respond'),
    path('market/calculate/',                views.calculate_market_price, name='calculate_market_price'),
    path('market/<int:pk>/',                 views.market_listing_detail,  name='market_listing_detail'),
]
